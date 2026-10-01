package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

//go:embed grafana_capabilities.json
var grafanaCapabilitiesJSON []byte

type resultRecord struct {
	tenant  string
	value   any
	expires time.Time
}

func (s *server) resultPut(tenant string, value any) string {
	s.resultsMu.Lock()
	defer s.resultsMu.Unlock()
	for ref, item := range s.results {
		if time.Now().After(item.expires) {
			delete(s.results, ref)
		}
	}
	ref := "gr_" + newRef()
	s.results[ref] = resultRecord{tenant: tenant, value: value, expires: time.Now().Add(15 * time.Minute)}
	return ref
}

func (s *server) resultGet(tenant, ref string) (any, error) {
	s.resultsMu.Lock()
	defer s.resultsMu.Unlock()
	item, ok := s.results[ref]
	if !ok || item.tenant != tenant || time.Now().After(item.expires) {
		delete(s.results, ref)
		return nil, errors.New("result reference is unavailable")
	}
	return item.value, nil
}

func grafanaHash(value any) (string, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return "", err
	}
	digest := sha256.Sum256(bytes.TrimSuffix(buffer.Bytes(), []byte("\n")))
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func grafanaCapabilities() (map[string]any, error) {
	var result map[string]any
	if err := json.Unmarshal(grafanaCapabilitiesJSON, &result); err != nil {
		return nil, err
	}
	if result["kind"] != "GrafanaCapabilityList" || result["items"] == nil {
		return nil, errors.New("Grafana capability manifest is invalid")
	}
	hash, err := grafanaHash(result)
	if err != nil {
		return nil, err
	}
	result["capabilityManifestHash"] = hash
	return result, nil
}

type grafanaBinding struct {
	OrgID       int    `json:"orgId"`
	TokenEnv    string `json:"tokenEnv"`
	TenantLabel string `json:"tenantLabel"`
	Namespace   string `json:"namespace"`
	TokenSecret string `json:"tokenSecret"`
}

func tenantGrafanaBinding(tenant string) (grafanaBinding, error) {
	configured := os.Getenv("GRAFANA_TENANT_BINDINGS_JSON")
	var bindings map[string]grafanaBinding
	if err := json.Unmarshal([]byte(configured), &bindings); err != nil {
		return grafanaBinding{}, errors.New("invalid server-side Grafana tenant bindings")
	}
	binding, ok := bindings[tenant]
	if !ok || binding.OrgID <= 1 || binding.TokenEnv == "" || binding.TenantLabel == "" {
		return grafanaBinding{}, errors.New("tenant has no verified Grafana organization binding")
	}
	return binding, nil
}

func kubeSecretValue(ctx context.Context, namespace, name, key string) (string, error) {
	path := "/api/v1/namespaces/" + url.PathEscape(namespace) + "/secrets/" + url.PathEscape(name)
	secret, err := kubeRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		return "", err
	}
	data := object(secret["data"])
	encoded, _ := data[key].(string)
	if encoded == "" {
		return "", errors.New("Kubernetes Secret key is unavailable")
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return "", err
	}
	return string(decoded), nil
}

type grafanaClient struct {
	base, authorization string
	orgID               int
}

type grafanaHTTPError struct{ Status int }

func (e grafanaHTTPError) Error() string {
	return fmt.Sprintf("Grafana request failed with HTTP %d", e.Status)
}

func tenantGrafana(ctx context.Context, tenant string, admin bool) (*grafanaClient, error) {
	binding, err := tenantGrafanaBinding(tenant)
	if err != nil {
		return nil, err
	}
	var authorization string
	if admin {
		username, password := os.Getenv("GRAFANA_ADMIN_USERNAME"), os.Getenv("GRAFANA_ADMIN_PASSWORD")
		if username == "" || password == "" {
			username, err = kubeSecretValue(ctx, "qianwen-ops", "panel-grafana-admin-recovery", "username")
			if err != nil {
				return nil, err
			}
			password, err = kubeSecretValue(ctx, "qianwen-ops", "panel-grafana-admin-recovery", "password")
			if err != nil {
				return nil, err
			}
		}
		authorization = "Basic " + base64.StdEncoding.EncodeToString([]byte(username+":"+password))
	} else {
		token := os.Getenv(binding.TokenEnv)
		if token == "" {
			if binding.Namespace == "" || binding.TokenSecret == "" {
				return nil, errors.New("tenant Grafana credential reference is unavailable")
			}
			token, err = kubeSecretValue(ctx, binding.Namespace, binding.TokenSecret, "token")
			if err != nil {
				return nil, err
			}
		}
		authorization = "Bearer " + token
	}
	base := strings.TrimRight(os.Getenv("GRAFANA_URL"), "/")
	if base == "" {
		base = "https://grafana.service.re8ch.com/grafana"
	}
	return &grafanaClient{base: base, authorization: authorization, orgID: binding.OrgID}, nil
}

func (c *grafanaClient) request(ctx context.Context, method, path string, payload any, timeout time.Duration, raw bool) (any, error) {
	var body io.Reader
	if payload != nil {
		encoded, _ := json.Marshal(payload)
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", c.authorization)
	req.Header.Set("X-Grafana-Org-Id", fmt.Sprint(c.orgID))
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := (&http.Client{Timeout: timeout}).Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, grafanaHTTPError{Status: response.StatusCode}
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if raw {
		return data, nil
	}
	if len(data) == 0 {
		return map[string]any{}, nil
	}
	var value any
	if err = json.Unmarshal(data, &value); err != nil {
		return nil, err
	}
	return value, nil
}

func datasourceCapability(item map[string]any) map[string]any {
	kind := asString(item, "type")
	kinds := []string{}
	switch {
	case strings.Contains(kind, "prometheus") || strings.Contains(kind, "victoriametrics-metrics"):
		kinds = []string{"promql"}
	case strings.Contains(kind, "logs") || kind == "loki":
		kinds = []string{"logsql"}
	case kind == "postgres" || kind == "mysql" || kind == "mssql":
		kinds = []string{"sql"}
	case kind == "jaeger":
		kinds = []string{"trace-search"}
	}
	interval := object(item["jsonData"])["timeInterval"]
	if interval == nil {
		interval = "1s"
	}
	return map[string]any{"uid": item["uid"], "name": item["name"], "type": kind, "queryKinds": kinds, "minimumInterval": interval, "maximumRange": "31d", "readOnly": true}
}

func grafanaDatasources(ctx context.Context, client *grafanaClient) ([]map[string]any, error) {
	value, err := client.request(ctx, http.MethodGet, "/api/datasources", nil, 30*time.Second, false)
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for _, raw := range slice(value) {
		row := object(raw)
		if asString(row, "uid") != "" && len(datasourceCapability(row)["queryKinds"].([]string)) != 0 {
			items = append(items, row)
		}
	}
	return items, nil
}
