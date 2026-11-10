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
	"github.com/modelcontextprotocol/go-sdk/mcp"
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
	Resource  string         `json:"resource"`
	Operation string         `json:"operation"`
	Owner     string         `json:"owner"`
	Repo      string         `json:"repo,omitempty"`
	Name      string         `json:"name,omitempty"`
	ID        int64          `json:"id,omitempty"`
	Body      map[string]any `json:"body,omitempty"`
	Apply     bool           `json:"apply,omitempty"`
	PlanHash  string         `json:"planHash,omitempty"`
}

var githubGovernanceName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,100}$`)

func githubGovernanceJWT() (string, error) {
	appID := strings.TrimSpace(os.Getenv("GITHUB_GOVERNANCE_APP_ID"))
	block, _ := pem.Decode([]byte(os.Getenv("GITHUB_GOVERNANCE_PRIVATE_KEY")))
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
func githubGovernanceToken(ctx context.Context, owner string) (string, time.Time, error) {
	jwt, e := githubGovernanceJWT()
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
	case "repository_ruleset:delete":
		method, path = http.MethodDelete, fmt.Sprintf("/repos/%s/%s/rulesets/%d", owner, in.Repo, in.ID)
	case "organization_ruleset:list":
		method, path = http.MethodGet, "/orgs/"+owner+"/rulesets"
	case "organization_ruleset:get":
		method, path = http.MethodGet, fmt.Sprintf("/orgs/%s/rulesets/%d", owner, in.ID)
	case "organization_ruleset:create":
		method, path = http.MethodPost, "/orgs/"+owner+"/rulesets"
	case "organization_ruleset:update":
		method, path = http.MethodPut, fmt.Sprintf("/orgs/%s/rulesets/%d", owner, in.ID)
	case "organization_ruleset:delete":
		method, path = http.MethodDelete, fmt.Sprintf("/orgs/%s/rulesets/%d", owner, in.ID)
	case "repository_variable:list":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/actions/variables"
	case "repository_variable:get":
		method, path = http.MethodGet, "/repos/"+owner+"/"+in.Repo+"/actions/variables/"+url.PathEscape(in.Name)
	case "repository_variable:create":
		method, path = http.MethodPost, "/repos/"+owner+"/"+in.Repo+"/actions/variables"
	case "repository_variable:update":
		method, path = http.MethodPatch, "/repos/"+owner+"/"+in.Repo+"/actions/variables/"+url.PathEscape(in.Name)
	case "repository_variable:delete":
		method, path = http.MethodDelete, "/repos/"+owner+"/"+in.Repo+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_variable:list":
		method, path = http.MethodGet, "/orgs/"+owner+"/actions/variables"
	case "organization_variable:get":
		method, path = http.MethodGet, "/orgs/"+owner+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_variable:create":
		method, path = http.MethodPost, "/orgs/"+owner+"/actions/variables"
	case "organization_variable:update":
		method, path = http.MethodPatch, "/orgs/"+owner+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization_variable:delete":
		method, path = http.MethodDelete, "/orgs/"+owner+"/actions/variables/"+url.PathEscape(in.Name)
	case "organization:get":
		method, path = http.MethodGet, "/orgs/"+owner
	case "organization:update":
		method, path = http.MethodPatch, "/orgs/"+owner
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
	if strings.Contains(in.Resource, "variable") && in.Operation != "list" && in.Name == "" {
		return "", "", nil, errors.New("variable name is required")
	}
	if strings.Contains(in.Resource, "ruleset") && (in.Operation == "get" || in.Operation == "update" || in.Operation == "delete") && in.ID <= 0 {
		return "", "", nil, errors.New("ruleset id is required")
	}
	body, _ := json.Marshal(in.Body)
	if method == http.MethodGet || method == http.MethodDelete {
		body = nil
	}
	return method, path, body, nil
}
func (s *server) registerGitHubGovernanceTool(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("github_governance"), Name: "github_governance", Description: "Plan, apply, and read allowlisted GitHub repository or organization rulesets, Actions variables, and organization settings through the Re8ch Enterprise Governance App. Re8ch Admin membership and the active governance service grant are required. Tokens and private keys are never returned."}, func(ctx context.Context, _ *mcp.CallToolRequest, in githubGovernanceInput) (*mcp.CallToolResult, any, error) {
		id, e := platformIdentity(ctx)
		if e != nil {
			return nil, nil, e
		}
		if id.Tenant != "admin" {
			return nil, nil, errors.New("Re8ch Admin organization context required")
		}
		if e = requireGrantedConsumable(ctx, id, githubGovernanceClass); e != nil {
			return nil, nil, e
		}
		method, path, body, e := githubGovernanceRequest(in)
		if e != nil {
			return nil, nil, e
		}
		mutation := method != http.MethodGet
		plan, _ := platformMutationPlan("github-governance", map[string]any{"method": method, "path": path, "body": in.Body})
		if mutation && !in.Apply {
			return nil, map[string]any{"applied": false, "method": method, "path": path, "planHash": plan}, nil
		}
		if mutation && in.PlanHash != plan {
			return nil, nil, errors.New("apply requires the exact planHash returned by preview")
		}
		token, expires, e := githubGovernanceToken(ctx, in.Owner)
		if e != nil {
			return nil, nil, e
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
			return nil, nil, e
		}
		defer resp.Body.Close()
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if resp.StatusCode/100 != 2 {
			return nil, nil, fmt.Errorf("GitHub governance API returned HTTP %d", resp.StatusCode)
		}
		var result any = map[string]any{}
		if len(data) > 0 {
			_ = json.Unmarshal(data, &result)
		}
		return nil, map[string]any{"applied": mutation, "result": result, "installationTokenExpiresAt": expires.UTC().Format(time.RFC3339), "credentialMaterialReturned": false, "auditRef": "github-governance://" + id.Subject + path}, nil
	})
}
