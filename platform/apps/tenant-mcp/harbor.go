package main

import (
	"context"
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

type harborClient struct {
	base, username, password string
	http                     *http.Client
}

func tenantHarbor(ctx context.Context, tenant, reference string) (*harborClient, string, error) {
	provider, material, err := credentialMaterial(ctx, tenant, reference)
	if err != nil {
		return nil, "", err
	}
	if provider != "harbor" || material["username"] == "" || material["password"] == "" {
		return nil, "", errors.New("Harbor credential is unavailable")
	}
	base := strings.TrimRight(os.Getenv("HARBOR_URL"), "/")
	if base == "" {
		base = "https://registry.re8ch.com"
	}
	project := material["project"]
	if project == "" {
		project = tenant
	}
	return &harborClient{base: base + "/api/v2.0", username: material["username"], password: material["password"], http: &http.Client{Timeout: 30 * time.Second}}, project, nil
}

func sanitize(value any) any {
	sensitive := map[string]bool{"password": true, "secret": true, "token": true, "access_token": true, "refresh_token": true, "authorization": true}
	switch v := value.(type) {
	case map[string]any:
		out := map[string]any{}
		for key, item := range v {
			if sensitive[strings.ToLower(key)] {
				out[key] = "[REDACTED]"
			} else {
				out[key] = sanitize(item)
			}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = sanitize(item)
		}
		return out
	default:
		return value
	}
}

func (c *harborClient) request(ctx context.Context, method, path string, payload any, sanitizeResponse bool) (any, error) {
	var body io.Reader
	if payload != nil {
		raw, _ := json.Marshal(payload)
		body = strings.NewReader(string(raw))
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, body)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(c.username, c.password)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(req)
	if err != nil {
		return nil, errors.New("Harbor API is unreachable")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 204 {
		return nil, fmt.Errorf("Harbor API returned HTTP %d", response.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		return map[string]any{}, nil
	}
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return map[string]any{"status": response.StatusCode}, nil
	}
	if sanitizeResponse {
		return sanitize(value), nil
	}
	return value, nil
}

func (c *harborClient) paged(ctx context.Context, path string) ([]any, error) {
	items := []any{}
	for page := 1; page <= 1000; page++ {
		separator := "?"
		if strings.Contains(path, "?") {
			separator = "&"
		}
		value, err := c.request(ctx, http.MethodGet, fmt.Sprintf("%s%spage=%d&page_size=100", path, separator, page), nil, true)
		if err != nil {
			return nil, err
		}
		batch, ok := value.([]any)
		if !ok || len(batch) == 0 {
			break
		}
		items = append(items, batch...)
		if len(batch) < 100 {
			break
		}
	}
	return items, nil
}

func harborArtifactPath(project, repository, reference string) string {
	return "/projects/" + url.PathEscape(project) + "/repositories/" + url.PathEscape(repository) + "/artifacts/" + url.PathEscape(reference)
}

func mutationPreview(resource string, before, after any, apply bool, rollback any) map[string]any {
	return map[string]any{"resource": resource, "applied": apply, "before": before, "after": after, "rollback": rollback, "safety": "explicit apply=true required; inputs are allowlisted and bounded"}
}

func (c *harborClient) createTag(ctx context.Context, project, repository, reference, tag string) error {
	validTag := regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,254}$`)
	if !validTag.MatchString(tag) {
		return errors.New("invalid OCI tag")
	}
	_, err := c.request(ctx, http.MethodPost, harborArtifactPath(project, repository, reference)+"/tags", map[string]string{"name": tag}, true)
	return err
}

func (c *harborClient) deleteTag(ctx context.Context, project, repository, reference, tag string) error {
	_, err := c.request(ctx, http.MethodDelete, harborArtifactPath(project, repository, reference)+"/tags/"+url.PathEscape(tag), nil, true)
	return err
}

func supplyChainRequest(ctx context.Context, method, path string, payload any) (map[string]any, error) {
	base := strings.TrimRight(os.Getenv("HARBOR_SUPPLY_CHAIN_URL"), "/")
	if base == "" {
		base = "http://harbor-supply-chain.tenant-infrastructure.svc"
	}
	secret := os.Getenv("HARBOR_SUPPLY_CHAIN_TOKEN")
	if secret == "" {
		return nil, errors.New("supply-chain service credential is unavailable")
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
	req.Header.Set("Authorization", "Bearer "+secret)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return nil, errors.New("supply-chain worker is unreachable")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("supply-chain worker returned HTTP %d", response.StatusCode)
	}
	var result map[string]any
	if err = json.NewDecoder(io.LimitReader(response.Body, 4<<20)).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}
