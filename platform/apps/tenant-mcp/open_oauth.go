package main

import (
	"bytes"
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

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const litellmOAuthCallback = "https://litellm.service.re8ch.com/sso/callback"

var oauthApplicationID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var openOAuthHTTPClient = &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect denied") }}
var openOAuthCredentialStore = storeOpenOAuthCredential
var openOAuthCredentialReady = openOAuthCredentialExists

type openOAuthRegisterInput struct {
	OperationKey string `json:"operationKey" jsonschema:"Unique operation key, 1 to 200 characters; reuse only for an exact replay"`
}

func openOAuthBase() (string, error) {
	base := strings.TrimRight(os.Getenv("OPEN_OAUTH_URL"), "/")
	if base == "" {
		base = "http://supabase-identity-rest.supabase-identity.svc.cluster.local:3000"
	}
	parsed, err := url.Parse(base)
	if err != nil || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Hostname() == "" {
		return "", errors.New("OpenOAuth authority is not configured")
	}
	internal := parsed.Scheme == "http" && strings.HasSuffix(parsed.Hostname(), ".svc.cluster.local")
	if parsed.Scheme != "https" && !internal {
		return "", errors.New("OpenOAuth authority requires HTTPS or the fixed internal service")
	}
	return base, nil
}

func callOpenOAuthRegister(ctx context.Context, token, operationKey string) (map[string]any, error) {
	if strings.TrimSpace(operationKey) != operationKey || operationKey == "" || len(operationKey) > 200 {
		return nil, errors.New("operationKey must contain 1 to 200 trimmed characters")
	}
	base, err := openOAuthBase()
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"p_name":          "RE8CH LiteLLM",
		"p_redirect_uris": []string{litellmOAuthCallback},
		"p_operation_key": operationKey,
		"p_client_uri":    "https://litellm.service.re8ch.com",
		"p_client_type":   "confidential",
	}
	raw, _ := json.Marshal(payload)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base+"/rpc/create_oauth_application", bytes.NewReader(raw))
	if err != nil {
		return nil, errors.New("invalid OpenOAuth request")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	response, err := openOAuthHTTPClient.Do(req)
	if err != nil {
		return nil, errors.New("OpenOAuth registration failed; inspect authority health before retrying")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("OpenOAuth registration returned HTTP %d", response.StatusCode)
	}
	var result map[string]any
	if err = json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(&result); err != nil {
		return nil, errors.New("OpenOAuth registration returned invalid JSON")
	}
	return result, nil
}

func storeOpenOAuthCredential(ctx context.Context, tenant, clientID, clientSecret string) (string, error) {
	if tenant != "re8ch" || !oauthApplicationID.MatchString(clientID) || len(clientSecret) < 32 || len(clientSecret) > 256 {
		return "", errors.New("OpenOAuth credential is invalid")
	}
	name := "oauth-litellm-" + clientID
	namespace := os.Getenv("CREDENTIAL_NAMESPACE")
	if namespace == "" {
		namespace = "credential-broker"
	}
	manifest := map[string]any{
		"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
		"metadata": map[string]any{
			"name": name, "namespace": namespace,
			"labels": map[string]string{
				"credentials.re8ch.com/provider":   "open-oauth",
				"credentials.re8ch.com/tenant":     tenant,
				"credentials.re8ch.com/projection": "litellm-gotrue-sso",
			},
		},
		"stringData": map[string]string{"client-id": clientID, "client-secret": clientSecret},
	}
	if _, err := kubeRequest(ctx, http.MethodPost, "/api/v1/namespaces/"+url.PathEscape(namespace)+"/secrets", manifest); err != nil {
		return "", errors.New("credential broker write failed; rotate the OpenOAuth secret with a new operation key before retrying")
	}
	return "credential://open-oauth/" + tenant + "/" + clientID, nil
}

func openOAuthCredentialExists(ctx context.Context, tenant, clientID string) bool {
	if tenant != "re8ch" || !oauthApplicationID.MatchString(clientID) {
		return false
	}
	namespace := os.Getenv("CREDENTIAL_NAMESPACE")
	if namespace == "" {
		namespace = "credential-broker"
	}
	name := "oauth-litellm-" + clientID
	_, err := kubeRequest(ctx, http.MethodGet, "/api/v1/namespaces/"+url.PathEscape(namespace)+"/secrets/"+url.PathEscape(name), nil)
	return err == nil
}

func registerLitellmOAuth(ctx context.Context, id identity, operationKey string) (map[string]any, error) {
	if id.Tenant != "re8ch" || !id.PlatformAdmin || (id.Role != "owner" && id.Role != "admin" && id.Role != "operator") {
		return nil, errors.New("Re8ch platform administrator membership is required")
	}
	result, err := callOpenOAuthRegister(ctx, id.Token, operationKey)
	if err != nil {
		return nil, err
	}
	clientID, _ := result["clientId"].(string)
	owner, _ := result["ownerOrgRef"].(string)
	secret, _ := result["clientSecret"].(string)
	if owner != id.Tenant || !oauthApplicationID.MatchString(clientID) {
		return nil, errors.New("OpenOAuth authority returned an invalid owner or client")
	}
	reference := "credential://open-oauth/" + id.Tenant + "/" + clientID
	if secret != "" {
		reference, err = openOAuthCredentialStore(ctx, id.Tenant, clientID, secret)
		if err != nil {
			return nil, err
		}
	} else if replay, _ := result["idempotentReplay"].(bool); !replay {
		return nil, errors.New("OpenOAuth did not issue the expected confidential secret")
	} else if !openOAuthCredentialReady(ctx, id.Tenant, clientID) {
		return nil, errors.New("idempotent OpenOAuth replay has no brokered credential; rotate with a new operation key")
	}
	return map[string]any{
		"applicationId": clientID, "clientId": clientID, "ownerOrgRef": owner,
		"credentialRef": reference, "credentialMaterialReturned": false,
		"idempotentReplay": result["idempotentReplay"],
		"projectionTarget": "llm-gateway/litellm-gotrue-sso",
	}, nil
}

func (s *server) registerOpenOAuthTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{
		Name:        "oauth_application_register_litellm",
		Description: "Register the fixed enterprise LiteLLM callback through pg-identity OpenOAuth. The confidential secret is written directly to the approved Credential Broker and is never returned. Requires Re8ch platform administration.",
		Annotations: toolAnnotations("oauth_application_register_litellm"),
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input openOAuthRegisterInput) (*mcp.CallToolResult, any, error) {
		id, err := ownerScopedIdentityFrom(ctx)
		if err != nil {
			return nil, nil, err
		}
		value, err := registerLitellmOAuth(ctx, id, input.OperationKey)
		return nil, value, err
	})
}
