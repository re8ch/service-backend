package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
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

const githubGovernanceClass = "integration.github.enterprise-governance.v1"

type githubGovernanceInput struct {
	Capability string         `json:"capability,omitempty"`
	Resource   string         `json:"resource"`
	Operation  string         `json:"operation"`
	Owner      string         `json:"owner"`
	Repo       string         `json:"repo,omitempty"`
	Name       string         `json:"name,omitempty"`
	ID         int64          `json:"id,omitempty"`
	Body       map[string]any `json:"body,omitempty"`
	Apply      bool           `json:"apply,omitempty"`
	PlanHash   string         `json:"planHash,omitempty"`
}

var githubGovernanceCapabilities = map[string]string{
	"governance":    "GITHUB_GOVERNANCE",
	"delivery":      "GITHUB_DELIVERY",
	"security":      "GITHUB_SECURITY",
	"collaboration": "GITHUB_COLLABORATION",
	"developer-ai":  "GITHUB_DEVELOPER_AI",
	"identity":      "GITHUB_IDENTITY",
	"billing":       "GITHUB_BILLING",
}

var githubGovernanceName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,100}$`)

func validateGitHubCustomProperties(body map[string]any, schema bool) error {
	properties, ok := body["properties"].([]any)
	if !ok || len(properties) == 0 || len(properties) > 32 || len(body) != 1 {
		return errors.New("custom properties require one non-empty properties array with at most 32 entries")
	}
	for _, raw := range properties {
		property, ok := raw.(map[string]any)
		if !ok {
			return errors.New("custom property must be an object")
		}
		name, _ := property["property_name"].(string)
		if !githubGovernanceName.MatchString(name) {
			return errors.New("invalid custom property name")
		}
		if schema {
			allowed := map[string]bool{"property_name": true, "value_type": true, "required": true, "default_value": true, "description": true, "allowed_values": true}
			for field := range property {
				if !allowed[field] {
					return fmt.Errorf("custom property schema field %q is not allowlisted", field)
				}
			}
			valueType, _ := property["value_type"].(string)
			if !map[string]bool{"string": true, "single_select": true, "multi_select": true, "true_false": true}[valueType] {
				return errors.New("unsupported custom property value_type")
			}
		} else {
			for field := range property {
				if field != "property_name" && field != "value" {
					return fmt.Errorf("repository custom property field %q is not allowlisted", field)
				}
			}
			if _, exists := property["value"]; !exists {
				return errors.New("repository custom property value is required")
			}
		}
	}
	return nil
}

func githubGovernanceJWT(capability string) (string, error) {
	if capability == "" {
		capability = "governance"
	}
	prefix, ok := githubGovernanceCapabilities[capability]
	if !ok {
		return "", errors.New("unsupported GitHub governance capability")
	}
	appID := strings.TrimSpace(os.Getenv(prefix + "_APP_ID"))
	block, _ := pem.Decode([]byte(os.Getenv(prefix + "_PRIVATE_KEY")))
	if appID == "" || block == nil {
		return "", errors.New("GitHub governance credential is not configured")
	}
	var key *rsa.PrivateKey
	if k, e := x509.ParsePKCS1PrivateKey(block.Bytes); e == nil {
		key = k
	} else if k, e := x509.ParsePKCS8PrivateKey(block.Bytes); e == nil {
		key, _ = k.(*rsa.PrivateKey)
	}
	if key == nil {
		return "", errors.New("GitHub governance private key must be RSA")
	}
	now := time.Now().Unix()
	enc := base64.RawURLEncoding.EncodeToString
	h := enc([]byte(`{"alg":"RS256","typ":"JWT"}`))
	p, _ := json.Marshal(map[string]any{"iat": now - 60, "exp": now + 540, "iss": appID})
	unsigned := h + "." + enc(p)
	digest := sha256.Sum256([]byte(unsigned))
	sig, e := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if e != nil {
		return "", e
	}
	return unsigned + "." + enc(sig), nil
}
func githubGovernanceToken(ctx context.Context, capability, owner string) (string, time.Time, error) {
	jwt, e := githubGovernanceJWT(capability)
	if e != nil {
		return "", time.Time{}, e
	}
	owner = strings.ToLower(strings.TrimSpace(owner))
	if !githubGovernanceName.MatchString(owner) {
		return "", time.Time{}, errors.New("invalid GitHub organization name")
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://api.github.com/orgs/"+url.PathEscape(owner)+"/installation", nil)
	req.Header.Set("Authorization", "Bearer "+jwt)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	resp, e := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if e != nil {
		return "", time.Time{}, e
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return "", time.Time{}, fmt.Errorf("GitHub governance App is not installed for organization %q (HTTP %d)", owner, resp.StatusCode)
	}
	var installation struct {
		ID int64 `json:"id"`
	}
	if json.Unmarshal(data, &installation) != nil || installation.ID <= 0 {
		return "", time.Time{}, errors.New("invalid GitHub organization installation response")
	}
	req, _ = http.NewRequestWithContext(ctx, http.MethodPost, fmt.Sprintf("https://api.github.com/app/installations/%d/access_tokens", installation.ID), bytes.NewReader([]byte(`{}`)))
	req.Header.Set("Authorization", "Bearer "+jwt)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	resp, e = (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if e != nil {
		return "", time.Time{}, e
	}
	defer resp.Body.Close()
	data, _ = io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return "", time.Time{}, fmt.Errorf("GitHub installation token request returned HTTP %d", resp.StatusCode)
	}
	var out struct {
		Token     string `json:"token"`
		ExpiresAt string `json:"expires_at"`
	}
	if json.Unmarshal(data, &out) != nil || out.Token == "" {
		return "", time.Time{}, errors.New("invalid GitHub installation token response")
	}
	expires, _ := time.Parse(time.RFC3339, out.ExpiresAt)
	return out.Token, expires, nil
}
func githubGovernanceRequest(in githubGovernanceInput) (string, string, []byte, error) {
	if strings.EqualFold(strings.TrimSpace(in.Operation), "delete") {
		return "", "", nil, errors.New("delete operations are not available through GitHub governance")
	}
	owner := strings.ToLower(strings.TrimSpace(in.Owner))
	if !githubGovernanceName.MatchString(owner) {
		return "", "", nil, errors.New("invalid GitHub organization name")
	}
	if in.Repo != "" && !githubGovernanceName.MatchString(in.Repo) {
		return "", "", nil, errors.New("invalid repository name")
	}
	if in.Name != "" && !githubGovernanceName.MatchString(in.Name) {
		return "", "", nil, errors.New("invalid variable name")
	}
	method, path := "", ""
	key := in.Resource + ":" + in.Operation
	switch key {
	case "repository_ruleset:list":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/rulesets"
	case "repository_ruleset:get":
		method, path = http.MethodGet, fmt.Sprintf("/repos/%s/%s/rulesets/%d", owner, in.Repo, in.ID)
	case "repository_ruleset:create":
		method, path = http.MethodPost, "/repos/"+owner+"/"+in.Repo+"/rulesets"
	case "repository_ruleset:update":
		method, path = http.MethodPut, fmt.Sprintf("/repos/%s/%s/rulesets/%d", owner, in.Repo, in.ID)
	case "organization_ruleset:list":
		method, path = http.MethodGet, "/orgs/"+owner+"/rulesets"
	case "organization_ruleset:get":
		method, path = http.MethodGet, fmt.Sprintf("/orgs/%s/rulesets/%d", owner, in.ID)
	case "organization_ruleset:create":
		method, path = http.MethodPost, "/orgs/"+owner+"/rulesets"
	case "organization_ruleset:update":
		method, path = http.MethodPut, fmt.Sprintf("/orgs/%s/rulesets/%d", owner, in.ID)
	case "repository_variable:list":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/actions/variables"
	case "repository_variable:get":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/actions/variables/"+url.PathEscape(in.Name)
	case "repository_variable:create":
		method, path = http.MethodPost, "/repos/"+owner+"/"+in.Repo+"/actions/variables"
	case "repository_variable:update":
		method, path = http.MethodPatch, "/repos/"+owner+"/"+in.Repo+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_variable:list":
		method, path = http.MethodGet, "/orgs/"+owner+"/actions/variables"
	case "organization_variable:get":
		method, path = http.MethodGet, "/orgs/"+owner+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_variable:create":
		method, path = http.MethodPost, "/orgs/"+owner+"/actions/variables"
	case "organization_variable:update":
		method, path = http.MethodPatch, "/orgs/"+owner+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_custom_property_schema:list":
		method, path = http.MethodGet, "/orgs/"+owner+"/properties/schema"
	case "organization_custom_property_schema:update":
		method, path = http.MethodPatch, "/orgs/"+owner+"/properties/schema"
	case "repository_custom_property_values:list":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/properties/values"
	case "repository_custom_property_values:update":
		method, path = http.MethodPatch, "/repos/"+owner+"/"+in.Repo+"/properties/values"
	case "organization:get":
		method, path = http.MethodGet, "/orgs/"+owner
	case "organization:update":
		method, path = http.MethodPatch, "/orgs/"+owner
	case "workflow:dispatch":
		method, path = http.MethodPost, "/repos/"+owner+"/"+in.Repo+"/actions/workflows/"+url.PathEscape(in.Name)+"/dispatches"
	case "workflow_run:get":
		method, path = http.MethodGet, fmt.Sprintf("/repos/%s/%s/actions/runs/%d", owner, in.Repo, in.ID)
	case "workflow_run:rerun":
		method, path = http.MethodPost, fmt.Sprintf("/repos/%s/%s/actions/runs/%d/rerun", owner, in.Repo, in.ID)
	case "workflow_run:rerun_failed":
		method, path = http.MethodPost, fmt.Sprintf("/repos/%s/%s/actions/runs/%d/rerun-failed-jobs", owner, in.Repo, in.ID)
	default:
		return "", "", nil, errors.New("unsupported governance resource and operation")
	}
	if key == "organization:update" {
		allowed := map[string]bool{"name": true, "billing_email": true, "company": true, "blog": true, "location": true, "twitter_username": true, "description": true, "has_organization_projects": true, "has_repository_projects": true, "default_repository_permission": true, "members_can_create_repositories": true, "web_commit_signoff_required": true}
		for field := range in.Body {
			if !allowed[field] {
				return "", "", nil, fmt.Errorf("organization field %q is not allowlisted", field)
			}
		}
	}
	if strings.Contains(in.Resource, "repository") && in.Repo == "" {
		return "", "", nil, errors.New("repository is required")
	}
	if in.Resource == "organization_custom_property_schema" && in.Operation == "update" {
		if err := validateGitHubCustomProperties(in.Body, true); err != nil {
			return "", "", nil, err
		}
	}
	if in.Resource == "repository_custom_property_values" && in.Operation == "update" {
		if err := validateGitHubCustomProperties(in.Body, false); err != nil {
			return "", "", nil, err
		}
	}
	if (in.Resource == "workflow" || in.Resource == "workflow_run") && in.Repo == "" {
		return "", "", nil, errors.New("repository is required")
	}
	if in.Resource == "workflow" {
		if in.Name == "" {
			return "", "", nil, errors.New("workflow name is required")
		}
		ref, ok := in.Body["ref"].(string)
		if !ok || !githubGovernanceName.MatchString(ref) {
			return "", "", nil, errors.New("workflow dispatch requires a valid ref")
		}
		for field := range in.Body {
			if field != "ref" && field != "inputs" {
				return "", "", nil, fmt.Errorf("workflow dispatch field %q is not allowlisted", field)
			}
		}
	}
	if in.Resource == "workflow_run" && in.ID <= 0 {
		return "", "", nil, errors.New("workflow run id is required")
	}
	if strings.Contains(in.Resource, "variable") && in.Operation != "list" && in.Name == "" {
		return "", "", nil, errors.New("variable name is required")
	}
	if strings.Contains(in.Resource, "ruleset") && (in.Operation == "get" || in.Operation == "update" || in.Operation == "delete") && in.ID <= 0 {
		return "", "", nil, errors.New("ruleset id is required")
	}
	body, _ := json.Marshal(in.Body)
	if method == http.MethodGet {
		body = nil
	}
	return method, path, body, nil
}
func executeGitHubGovernance(ctx context.Context, id identity, in githubGovernanceInput) (any, error) {
	if id.Tenant != "admin" || id.Role != "admin" {
		return nil, errors.New("Re8ch Admin organization context required")
	}
	if e := requireGrantedConsumable(ctx, id, githubGovernanceClass); e != nil {
		return nil, e
	}
	method, path, body, e := githubGovernanceRequest(in)
	if e != nil {
		return nil, e
	}
	mutation := method != http.MethodGet
	plan, _ := platformMutationPlan("github-governance", map[string]any{"method": method, "path": path, "body": in.Body})
	if mutation && !in.Apply {
		return map[string]any{"applied": false, "method": method, "path": path, "planHash": plan, "credentialMaterialReturned": false}, nil
	}
	if mutation && in.PlanHash != plan {
		return nil, errors.New("apply requires the exact planHash returned by preview")
	}
	token, expires, e := githubGovernanceToken(ctx, in.Capability, in.Owner)
	if e != nil {
		return nil, e
	}
	req, _ := http.NewRequestWithContext(ctx, method, "https://api.github.com"+path, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, e := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if e != nil {
		return nil, e
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("GitHub governance API returned HTTP %d", resp.StatusCode)
	}
	var result any = map[string]any{}
	if len(data) > 0 {
		_ = json.Unmarshal(data, &result)
	}
	return map[string]any{"applied": mutation, "result": result, "installationTokenExpiresAt": expires.UTC().Format(time.RFC3339), "credentialMaterialReturned": false, "auditRef": "github-governance://" + id.Subject + path}, nil
}
