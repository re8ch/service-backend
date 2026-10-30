package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

var credentialRef = regexp.MustCompile(`^credential://(harbor|grafana)/([a-z0-9][a-z0-9._-]*)/([a-f0-9]{32})$`)

func openRedemptions(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if _, err = db.Exec(`PRAGMA busy_timeout=10000;
		CREATE TABLE IF NOT EXISTS credential_redemptions (token_hash TEXT PRIMARY KEY, credential_ref TEXT NOT NULL, tenant TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER);
		CREATE TABLE IF NOT EXISTS credential_adoptions (token_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER);`); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

func tokenDigest(token string) string {
	value := sha256.Sum256([]byte(token))
	return hex.EncodeToString(value[:])
}

func (s *server) consumeRedemption(token, table string) (string, string, error) {
	if s.redemptions == nil || token == "" || len(token) > 256 {
		return "", "", errors.New("expired_or_consumed")
	}
	tx, err := s.redemptions.Begin()
	if err != nil {
		return "", "", err
	}
	defer tx.Rollback()
	digest := tokenDigest(token)
	var reference, tenant string
	var expires int64
	var consumed sql.NullInt64
	if table == "credential_adoptions" {
		err = tx.QueryRow(`SELECT tenant,expires_at,consumed_at FROM credential_adoptions WHERE token_hash=?`, digest).Scan(&tenant, &expires, &consumed)
	} else {
		err = tx.QueryRow(`SELECT credential_ref,tenant,expires_at,consumed_at FROM credential_redemptions WHERE token_hash=?`, digest).Scan(&reference, &tenant, &expires, &consumed)
	}
	if err != nil || consumed.Valid || expires < time.Now().Unix() {
		return "", "", errors.New("expired_or_consumed")
	}
	statement := `UPDATE credential_redemptions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL`
	if table == "credential_adoptions" {
		statement = `UPDATE credential_adoptions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL`
	}
	result, err := tx.Exec(statement, time.Now().Unix(), digest)
	if err != nil {
		return "", "", err
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return "", "", errors.New("expired_or_consumed")
	}
	if err = tx.Commit(); err != nil {
		return "", "", err
	}
	return reference, tenant, nil
}

func (s *server) issueRedemption(reference, tenant string) (map[string]any, error) {
	if s.redemptions == nil {
		return nil, errors.New("redemption registry unavailable")
	}
	raw := make([]byte, 48)
	if _, err := rand.Read(raw); err != nil {
		return nil, err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	expires := time.Now().Unix() + 600
	if _, err := s.redemptions.Exec(`INSERT INTO credential_redemptions VALUES(?,?,?,?,NULL)`, tokenDigest(token), reference, tenant, expires); err != nil {
		return nil, err
	}
	origin := strings.TrimRight(os.Getenv("PUBLIC_ORIGIN"), "/")
	if origin == "" {
		origin = "https://tools.re8ch.com"
	}
	return map[string]any{"credential_ref": reference, "redemption_url": origin + "/credentials/redeem#" + token, "expires_at": expires, "single_use": true}, nil
}

func kubeClient() (*http.Client, string, string, error) {
	host := os.Getenv("KUBERNETES_SERVICE_HOST")
	if host == "" {
		return nil, "", "", errors.New("Kubernetes API unavailable")
	}
	port := os.Getenv("KUBERNETES_SERVICE_PORT")
	if port == "" {
		port = "443"
	}
	token, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/token")
	if err != nil {
		return nil, "", "", err
	}
	ca, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
	if err != nil {
		return nil, "", "", err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, "", "", errors.New("invalid Kubernetes CA")
	}
	client := &http.Client{Timeout: 30 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}}}
	return client, "https://" + host + ":" + port, strings.TrimSpace(string(token)), nil
}

type kubeResponseError struct{ Status int }

func (e kubeResponseError) Error() string {
	return fmt.Sprintf("Kubernetes API returned HTTP %d", e.Status)
}

func kubeRequest(ctx context.Context, method, path string, payload any) (map[string]any, error) {
	client, base, token, err := kubeClient()
	if err != nil {
		return nil, err
	}
	var body io.Reader
	if payload != nil {
		data, _ := json.Marshal(payload)
		body = strings.NewReader(string(data))
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, kubeResponseError{Status: response.StatusCode}
	}
	var result map[string]any
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func controlRequest(ctx context.Context, tenant, path string) (map[string]any, error) {
	return controlRequestBody(ctx, tenant, path, nil)
}

func controlRequestBody(ctx context.Context, tenant, path string, payload any) (map[string]any, error) {
	method := http.MethodGet
	if payload != nil {
		method = http.MethodPost
	}
	return controlRequestMethod(ctx, tenant, path, method, payload)
}

func controlRequestMethod(ctx context.Context, tenant, path, method string, payload any) (map[string]any, error) {
	base, secret := strings.TrimRight(os.Getenv("TENANT_CONTROL_URL"), "/"), os.Getenv("TENANT_CLAIM_SERVICE_TOKEN")
	if base == "" || secret == "" {
		return nil, errors.New("tenant claim controller unavailable")
	}
	var body io.Reader
	if payload != nil {
		raw, _ := json.Marshal(payload)
		body = strings.NewReader(string(raw))
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-Caller", "tenant")
	req.Header.Set("X-Tenant-Ref", tenant)
	req.Header.Set("X-Tenant-Service-Token", secret)
	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, controlHTTPError{Status: response.StatusCode}
	}
	var value map[string]any
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}

type controlHTTPError struct{ Status int }

func (e controlHTTPError) Error() string {
	return fmt.Sprintf("tenant claim controller returned HTTP %d", e.Status)
}

func readyHumanCredential(ctx context.Context, tenant, reference string) error {
	match := credentialRef.FindStringSubmatch(reference)
	if match == nil || match[2] != tenant {
		return errors.New("cross-tenant credential reference denied")
	}
	serviceClass := "registry.harbor.v1"
	if match[1] == "grafana" {
		serviceClass = "observability.grafana.v1"
	}
	result, err := controlRequest(ctx, tenant, "/v1/services")
	if err != nil {
		return err
	}
	items, _ := result["items"].([]any)
	for _, raw := range items {
		item, _ := raw.(map[string]any)
		spec, _ := item["spec"].(map[string]any)
		status, _ := item["status"].(map[string]any)
		if spec["serviceClass"] == serviceClass && spec["lifecycle"] == "Requested" && status["phase"] == "Ready" && status["humanCredentialRef"] == reference {
			return nil
		}
	}
	return errors.New("human credential has no ready tenant service instance")
}

func credentialMaterial(ctx context.Context, tenant, reference string) (string, map[string]string, error) {
	match := credentialRef.FindStringSubmatch(reference)
	if match == nil || match[2] != tenant {
		return "", nil, errors.New("cross-tenant credential reference denied")
	}
	namespace := os.Getenv("CREDENTIAL_NAMESPACE")
	if namespace == "" {
		namespace = "credential-broker"
	}
	name := match[1] + "-" + match[3]
	path := "/api/v1/namespaces/" + url.PathEscape(namespace) + "/secrets/" + url.PathEscape(name)
	secret, err := kubeRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		return "", nil, err
	}
	metadata, _ := secret["metadata"].(map[string]any)
	labels, _ := metadata["labels"].(map[string]any)
	if labels["credentials.re8ch.com/tenant"] != tenant || labels["credentials.re8ch.com/provider"] != match[1] {
		return "", nil, errors.New("credential tenant or provider label mismatch")
	}
	data, _ := secret["data"].(map[string]any)
	material := map[string]string{}
	for key, value := range data {
		encoded, ok := value.(string)
		if !ok {
			return "", nil, errors.New("invalid credential data")
		}
		decoded, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil {
			return "", nil, err
		}
		material[key] = string(decoded)
	}
	return match[1], material, nil
}
