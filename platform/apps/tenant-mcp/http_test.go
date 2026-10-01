package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func fixtureToken() string {
	claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": "user-1", "exp": time.Now().Add(time.Hour).Unix()})
	return "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
}

func TestMCPIdentityAndDynamicSkill(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+fixtureToken() {
			t.Error("missing GoTrue token")
		}
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Tenant-Service-Token") != "claim-secret" {
			t.Error("missing tenant service token")
		}
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		writeJSON(w, 200, map[string]string{"tenantRef": body["tenantRef"], "role": "operator", "defaultNamespace": "acme"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("SERVICE_API_TOKEN", "backend-secret")
	t.Setenv("RE8CH_TOOL_AUDIENCE", "re8ch-tenant")

	ref, err := s.submit(request{Tenant: "admin", TargetTenant: "acme", Subject: "reviewer", Name: "special-order", Description: "Special order", Workflow: "Call approved tools for special order."}, true)
	if err != nil {
		t.Fatal(err)
	}
	handler := s.handler()
	call := func(token, selected, method string, params any) (int, map[string]any) {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Authorization", "Bearer "+token)
		if selected != "" {
			req.Header.Set("X-RE8CH-Tenant", selected)
		}
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		var decoded map[string]any
		_ = json.Unmarshal(response.Body.Bytes(), &decoded)
		return response.Code, decoded
	}
	if status, _ := call("invalid", "acme", "tools/list", map[string]any{}); status != 401 {
		t.Fatalf("invalid token status = %d", status)
	}
	if status, body := call("backend-secret", "", "tools/list", map[string]any{}); status != 200 || body["result"] == nil {
		t.Fatalf("ToolHive service discovery failed: %d %v", status, body)
	}
	if status, body := call("backend-secret", "", "resources/list", map[string]any{}); status != 200 || len(body["result"].(map[string]any)["resources"].([]any)) != 0 {
		t.Fatalf("service credential saw tenant resources: %d %v", status, body)
	}
	if status, body := call(fixtureToken(), "acme", "resources/list", map[string]any{}); status != 200 || len(body["result"].(map[string]any)["resources"].([]any)) != 9 {
		t.Fatalf("tenant resource inventory changed: %d %v", status, body)
	}
	if status, body := call("backend-secret", "", "tools/call", map[string]any{"name": "dynamic_skill_search", "arguments": map[string]string{"query": "special order"}}); status != 200 || body["result"] == nil || body["result"].(map[string]any)["isError"] != true {
		t.Fatalf("service invocation was not denied: %d %v", status, body)
	}
	if status, body := call(fixtureToken(), "acme", "tools/call", map[string]any{"name": "dynamic_skill_load", "arguments": map[string]string{"skillRef": ref.(skill).Ref, "instruction": "Handle an order"}}); status != 200 || body["result"] == nil || body["result"].(map[string]any)["isError"] == true {
		t.Fatalf("tenant load failed: %d %v", status, body)
	}
	if status, body := call(fixtureToken(), "other", "tools/call", map[string]any{"name": "dynamic_skill_load", "arguments": map[string]string{"skillRef": ref.(skill).Ref, "instruction": "Handle an order"}}); status != 200 || body["result"] == nil || body["result"].(map[string]any)["isError"] != true {
		t.Fatalf("cross-tenant load succeeded: %d %v", status, body)
	}
}

func TestMCPRejectedBearerReturnsOAuthChallenge(t *testing.T) {
	s := testServer(t)
	t.Setenv("PUBLIC_RESOURCE_METADATA_URL", "https://tools.example.test/.well-known/oauth-protected-resource/mcp")
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`))
	req.Header.Set("Authorization", "Bearer invalid")
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusUnauthorized || !strings.Contains(response.Header().Get("WWW-Authenticate"), `resource_metadata="https://tools.example.test/.well-known/oauth-protected-resource/mcp"`) {
		t.Fatalf("invalid bearer must return a discoverable OAuth challenge: %d %q", response.Code, response.Header().Get("WWW-Authenticate"))
	}
}

func TestMCPVerifiedBearerWithoutTenantIsForbidden(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "select an active tenant"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`))
	req.Header.Set("Authorization", "Bearer "+fixtureToken())
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusForbidden || response.Header().Get("WWW-Authenticate") != "" {
		t.Fatalf("verified user without a tenant must not be asked to log in again: %d %q", response.Code, response.Header().Get("WWW-Authenticate"))
	}
}

func TestTokenExchangePreservesUserJWT(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { writeJSON(w, 200, map[string]string{"id": "user-1"}) }))
	defer goTrue.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("RE8CH_TOOL_AUDIENCE", "re8ch-tenant")
	form := url.Values{"grant_type": {"urn:ietf:params:oauth:grant-type:token-exchange"}, "subject_token_type": {"urn:ietf:params:oauth:token-type:access_token"}, "audience": {"re8ch-tenant"}, "subject_token": {fixtureToken()}}
	req := httptest.NewRequest("POST", "/oauth/token", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != 200 || !strings.Contains(response.Body.String(), fmt.Sprintf(`"access_token":"%s"`, fixtureToken())) {
		t.Fatalf("exchange failed: %d %s", response.Code, response.Body.String())
	}
}

func TestSkillAdminEndpointRequiresSeparateCredential(t *testing.T) {
	s := testServer(t)
	t.Setenv("DYNAMIC_SKILL_ADMIN_TOKEN", "review-secret")
	handler := s.adminHandler()
	call := func(token, subject string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/internal/dynamic-skills", strings.NewReader(`{"action":"publish","targetTenant":"*","name":"special-order","description":"Special order","workflow":"Use the reviewed procedure."}`))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("X-RE8CH-Admin-Subject", subject)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		return response
	}
	if response := call("backend-secret", "admin-user"); response.Code != 401 {
		t.Fatalf("backend token published a workflow: %d", response.Code)
	}
	if response := call("review-secret", ""); response.Code != 400 {
		t.Fatalf("missing admin subject accepted: %d", response.Code)
	}
	if response := call("review-secret", "admin-user"); response.Code != 200 {
		t.Fatalf("admin publish failed: %d %s", response.Code, response.Body.String())
	}
}

func TestPublishedToolCatalogParity(t *testing.T) {
	s := testServer(t)
	t.Setenv("SERVICE_API_TOKEN", "backend-secret")
	request := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}`))
	request.Header.Set("Authorization", "Bearer backend-secret")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json, text/event-stream")
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("tools/list status %d: %s", response.Code, response.Body.String())
	}
	var result struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	legacy, err := legacyDefinitions()
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, item := range result.Result.Tools {
		names[item.Name] = true
	}
	for name := range legacy {
		if !names[name] {
			t.Errorf("missing tenant tool %s", name)
		}
	}
	for _, name := range []string{"dynamic_skill_search", "dynamic_skill_load", "dynamic_skill_submit"} {
		if !names[name] {
			t.Errorf("missing dynamic skill tool %s", name)
		}
	}
	if len(names) != len(legacy)+3 {
		t.Fatalf("published %d tools, expected %d", len(names), len(legacy)+3)
	}
}

func TestLegacyMCPInitializeAndOAuthMetadata(t *testing.T) {
	s := testServer(t)
	t.Setenv("SERVICE_API_TOKEN", "backend-secret")
	handler := s.handler()
	request := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"contract-test","version":"1"}}}`))
	request.Header.Set("Authorization", "Bearer backend-secret")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json, text/event-stream")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("initialize status %d: %s", response.Code, response.Body.String())
	}
	var result map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if object(result["result"])["protocolVersion"] != "2025-06-18" {
		t.Fatalf("protocol version changed: %v", result)
	}
	request = httptest.NewRequest("GET", "/.well-known/oauth-protected-resource/mcp", nil)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 || !strings.Contains(response.Body.String(), supabaseIssuer) {
		t.Fatalf("OAuth metadata failed: %d %s", response.Code, response.Body.String())
	}
}
