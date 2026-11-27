package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOpenOAuthRegisterUsesFixedConfidentialLiteLLMContract(t *testing.T) {
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rpc/create_oauth_application" || r.Header.Get("Authorization") != "Bearer user-token" {
			t.Fatalf("unexpected request: %s %s", r.URL.Path, r.Header.Get("Authorization"))
		}
		var input map[string]any
		if json.NewDecoder(r.Body).Decode(&input) != nil {
			t.Fatal("invalid request JSON")
		}
		if input["p_client_type"] != "confidential" || input["p_client_uri"] != "https://litellm.service.re8ch.com" || input["p_operation_key"] != "issue-3325-initial" {
			t.Fatalf("unexpected request: %#v", input)
		}
		redirects := input["p_redirect_uris"].([]any)
		if len(redirects) != 1 || redirects[0] != litellmOAuthCallback {
			t.Fatalf("unexpected redirects: %#v", redirects)
		}
		writeJSON(w, 200, map[string]any{"clientId": "123e4567-e89b-42d3-a456-426614174000"})
	}))
	defer upstream.Close()
	originalClient := openOAuthHTTPClient
	openOAuthHTTPClient = upstream.Client()
	defer func() { openOAuthHTTPClient = originalClient }()
	t.Setenv("OPEN_OAUTH_URL", upstream.URL)
	result, err := callOpenOAuthRegister(context.Background(), "user-token", "issue-3325-initial")
	if err != nil || result["clientId"] == "" {
		t.Fatalf("registration failed: %#v %v", result, err)
	}
}

func TestOpenOAuthRegistrationNeverReturnsSecret(t *testing.T) {
	original := openOAuthCredentialStore
	originalReady := openOAuthCredentialReady
	defer func() { openOAuthCredentialStore = original; openOAuthCredentialReady = originalReady }()
	openOAuthCredentialReady = func(context.Context, string, string) bool { return true }
	stored := ""
	openOAuthCredentialStore = func(_ context.Context, tenant, clientID, secret string) (string, error) {
		stored = tenant + ":" + clientID + ":" + secret
		return "credential://open-oauth/re8ch/123e4567-e89b-42d3-a456-426614174000", nil
	}
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{
			"clientId":         "123e4567-e89b-42d3-a456-426614174000",
			"ownerOrgRef":      "re8ch",
			"clientSecret":     "super-sensitive-confidential-secret-value",
			"idempotentReplay": false,
		})
	}))
	defer upstream.Close()
	originalClient := openOAuthHTTPClient
	openOAuthHTTPClient = upstream.Client()
	defer func() { openOAuthHTTPClient = originalClient }()
	t.Setenv("OPEN_OAUTH_URL", upstream.URL)

	result, err := registerLitellmOAuth(context.Background(), identity{Subject: "user", Tenant: "re8ch", Role: "admin", Token: "token", PlatformAdmin: true}, "issue-3325-initial")
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(result)
	if !strings.Contains(stored, "super-sensitive-confidential-secret-value") || strings.Contains(string(raw), "super-sensitive") {
		t.Fatalf("unsafe credential handling: stored=%t result=%s", stored != "", raw)
	}
	if !strings.Contains(string(raw), "credentialMaterialReturned") || !strings.Contains(string(raw), "litellm-gotrue-sso") {
		t.Fatalf("missing safe projection result: %s", raw)
	}
}
