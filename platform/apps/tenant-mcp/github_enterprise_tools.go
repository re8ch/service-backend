package main

import (
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
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	githubEnterpriseSlug       = "re8ch"
	githubCredentialNamespace  = "tenant-infrastructure"
	githubCredentialSecretName = "re8ch-enterprise-governance-github-app"
)

var (
	githubAPIBaseURL               = "https://api.github.com"
	githubClientID                 = regexp.MustCompile(`^Iv[0-9A-Za-z]{18,}$`)
	githubOrgName                  = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$`)
	githubRepoName                 = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,100}$`)
	loadGitHubEnterpriseCredential = githubEnterpriseCredentialFromSecret
)

type githubEnterpriseCredential struct {
	AppID          string
	InstallationID string
	PrivateKeyPEM  []byte
}

type githubEnterpriseAppsInput struct {
	Action              string   `json:"action"`
	Organization        string   `json:"organization,omitempty"`
	ClientID            string   `json:"clientId,omitempty"`
	RepositorySelection string   `json:"repositorySelection,omitempty"`
	Repositories        []string `json:"repositories,omitempty"`
	Apply               bool     `json:"apply,omitempty"`
	PlanHash            string   `json:"planHash,omitempty"`
}

func githubEnterpriseCredentialFromSecret(ctx context.Context) (githubEnterpriseCredential, error) {
	secret, err := kubeRequest(ctx, http.MethodGet, "/api/v1/namespaces/"+githubCredentialNamespace+"/secrets/"+githubCredentialSecretName, nil)
	if err != nil {
		return githubEnterpriseCredential{}, err
	}
	data, ok := secret["data"].(map[string]any)
	if !ok {
		return githubEnterpriseCredential{}, errors.New("invalid GitHub App credential Secret")
	}
	decode := func(name string) ([]byte, error) {
		value, ok := data[name].(string)
		if !ok || value == "" {
			return nil, fmt.Errorf("GitHub App credential Secret is missing %s", name)
		}
		decoded, err := base64.StdEncoding.DecodeString(value)
		if err != nil || len(decoded) == 0 {
			return nil, fmt.Errorf("GitHub App credential Secret contains invalid %s", name)
		}
		return decoded, nil
	}
	appID, err := decode("app-id")
	if err != nil {
		return githubEnterpriseCredential{}, err
	}
	installationID, err := decode("installation-id")
	if err != nil {
		return githubEnterpriseCredential{}, err
	}
	privateKey, err := decode("private-key.pem")
	if err != nil {
		return githubEnterpriseCredential{}, err
	}
	if _, err := strconv.ParseInt(string(appID), 10, 64); err != nil {
		return githubEnterpriseCredential{}, errors.New("GitHub App ID is invalid")
	}
	if _, err := strconv.ParseInt(string(installationID), 10, 64); err != nil {
		return githubEnterpriseCredential{}, errors.New("GitHub App installation ID is invalid")
	}
	return githubEnterpriseCredential{AppID: string(appID), InstallationID: string(installationID), PrivateKeyPEM: privateKey}, nil
}

func githubAppJWT(credential githubEnterpriseCredential, now time.Time) (string, error) {
	block, _ := pem.Decode(credential.PrivateKeyPEM)
	if block == nil {
		return "", errors.New("GitHub App private key is invalid")
	}
	var key *rsa.PrivateKey
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err == nil {
		key, _ = parsed.(*rsa.PrivateKey)
	} else if parsedPKCS1, pkcs1Err := x509.ParsePKCS1PrivateKey(block.Bytes); pkcs1Err == nil {
		key = parsedPKCS1
	}
	if key == nil {
		return "", errors.New("GitHub App private key is not RSA")
	}
	encode := func(value any) string {
		raw, _ := json.Marshal(value)
		return base64.RawURLEncoding.EncodeToString(raw)
	}
	header := encode(map[string]string{"alg": "RS256", "typ": "JWT"})
	payload := encode(map[string]any{"iat": now.Add(-60 * time.Second).Unix(), "exp": now.Add(9 * time.Minute).Unix(), "iss": credential.AppID})
	unsigned := header + "." + payload
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		return "", err
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

func githubJSONRequest(ctx context.Context, token, method, path string, payload any) (any, error) {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = strings.NewReader(string(raw))
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(githubAPIBaseURL, "/")+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2026-03-10")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	limited := io.LimitReader(response.Body, 4<<20)
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("GitHub API returned HTTP %d", response.StatusCode)
	}
	var result any
	if err := json.NewDecoder(limited).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func githubEnterpriseInstallationToken(ctx context.Context) (string, error) {
	credential, err := loadGitHubEnterpriseCredential(ctx)
	if err != nil {
		return "", err
	}
	jwt, err := githubAppJWT(credential, time.Now())
	if err != nil {
		return "", err
	}
	result, err := githubJSONRequest(ctx, jwt, http.MethodPost, "/app/installations/"+url.PathEscape(credential.InstallationID)+"/access_tokens", map[string]any{})
	if err != nil {
		return "", err
	}
	object, ok := result.(map[string]any)
	if !ok {
		return "", errors.New("GitHub returned an invalid installation token response")
	}
	token, _ := object["token"].(string)
	if token == "" {
		return "", errors.New("GitHub did not return an installation token")
	}
	return token, nil
}

func validateGitHubEnterpriseAppsInput(input githubEnterpriseAppsInput) (map[string]any, error) {
	if input.Organization != "" && !githubOrgName.MatchString(input.Organization) {
		return nil, errors.New("invalid GitHub organization")
	}
	switch input.Action {
	case "list_organizations":
		if input.Organization != "" || input.ClientID != "" || input.Apply || len(input.Repositories) != 0 {
			return nil, errors.New("list_organizations does not accept installation fields")
		}
		return nil, nil
	case "list_installations":
		if input.Organization == "" || input.ClientID != "" || input.Apply || len(input.Repositories) != 0 {
			return nil, errors.New("list_installations requires only organization")
		}
		return nil, nil
	case "install":
	default:
		return nil, errors.New("action must be list_organizations, list_installations, or install")
	}
	if input.Organization == "" || !githubClientID.MatchString(input.ClientID) {
		return nil, errors.New("install requires organization and a valid GitHub App clientId")
	}
	if input.RepositorySelection != "all" && input.RepositorySelection != "selected" && input.RepositorySelection != "none" {
		return nil, errors.New("repositorySelection must be all, selected, or none")
	}
	if input.RepositorySelection == "selected" && len(input.Repositories) == 0 {
		return nil, errors.New("selected repository access requires repositories")
	}
	if input.RepositorySelection != "selected" && len(input.Repositories) != 0 {
		return nil, errors.New("repositories are accepted only with selected access")
	}
	if len(input.Repositories) > 100 {
		return nil, errors.New("at most 100 repositories may be installed at once")
	}
	for _, repository := range input.Repositories {
		if !githubRepoName.MatchString(repository) {
			return nil, errors.New("invalid repository name")
		}
	}
	payload := map[string]any{"client_id": input.ClientID, "repository_selection": input.RepositorySelection}
	if len(input.Repositories) != 0 {
		payload["repositories"] = input.Repositories
	}
	return payload, nil
}

func (s *server) registerGitHubEnterpriseTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_github_enterprise_apps"), Name: "platform_github_enterprise_apps", Description: "Translate reviewed Re8ch enterprise GitHub App intent into the fixed GitHub organization-installations API. Lists enterprise organizations/installations or previews and applies one App installation. Uses a short-lived Governance App installation token internally; tokens and private keys are never returned."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input githubEnterpriseAppsInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			payload, err := validateGitHubEnterpriseAppsInput(input)
			if err != nil {
				return nil, nil, err
			}
			if input.Action == "install" {
				plan, err := platformMutationPlan("github-enterprise-app-install", map[string]any{"enterprise": githubEnterpriseSlug, "organization": input.Organization, "payload": payload})
				if err != nil {
					return nil, nil, err
				}
				if !input.Apply {
					return nil, map[string]any{"applied": false, "planHash": plan, "enterprise": githubEnterpriseSlug, "organization": input.Organization, "request": payload}, nil
				}
				if input.PlanHash != plan {
					return nil, nil, errors.New("apply requires the exact planHash returned by preview")
				}
			}
			token, err := githubEnterpriseInstallationToken(ctx)
			if err != nil {
				return nil, nil, err
			}
			path := "/enterprises/" + githubEnterpriseSlug + "/apps/organizations"
			method := http.MethodGet
			var requestBody any
			switch input.Action {
			case "list_organizations":
				path += "?per_page=100"
			case "list_installations":
				path += "/" + url.PathEscape(input.Organization) + "/installations?per_page=100"
			case "install":
				path += "/" + url.PathEscape(input.Organization) + "/installations"
				method, requestBody = http.MethodPost, payload
			}
			result, err := githubJSONRequest(ctx, token, method, path, requestBody)
			if err != nil {
				return nil, nil, err
			}
			return nil, map[string]any{"enterprise": githubEnterpriseSlug, "action": input.Action, "result": result}, nil
		})
}
