package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestMCPRetriesTransientGoTrueFailure(t *testing.T) {
	s := testServer(t)
	var calls atomic.Int32
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if calls.Add(1) == 1 {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "database unavailable"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"tenantRef": "acme", "role": "operator", "defaultNamespace": "acme"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}`))
	req.Header.Set("Authorization", "Bearer "+fixtureToken())
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusOK || calls.Load() != 2 || !strings.Contains(response.Body.String(), "service_catalog_list") {
		t.Fatalf("transient GoTrue failure was not retried: status=%d calls=%d body=%s", response.Code, calls.Load(), response.Body.String())
	}
}

func TestMCPPersistentGoTrueFailureReturnsServiceUnavailable(t *testing.T) {
	s := testServer(t)
	var calls atomic.Int32
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "database unavailable"})
	}))
	defer goTrue.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`))
	req.Header.Set("Authorization", "Bearer "+fixtureToken())
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusServiceUnavailable || calls.Load() != 2 || response.Header().Get("WWW-Authenticate") != "" {
		t.Fatalf("GoTrue outage was mistaken for an invalid token: status=%d calls=%d challenge=%q body=%s", response.Code, calls.Load(), response.Header().Get("WWW-Authenticate"), response.Body.String())
	}
}

func fixtureToken() string {
	return fixtureTokenFor("user-1")
}

func fixtureTokenFor(subject string) string {
	claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": subject, "exp": time.Now().Add(time.Hour).Unix()})
	return "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
}

func TestMCPPersonalSkillCannotBeLoadedByAnotherAuthenticatedUser(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		claims, err := decodeClaims(token)
		if err != nil {
			writeJSON(w, 401, map[string]string{"error": "invalid_token"})
			return
		}
		writeJSON(w, 200, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	call := func(subject, method string, params any) map[string]any {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer "+fixtureTokenFor(subject))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != 200 {
			t.Fatalf("MCP %s failed: %d %s", method, response.Code, response.Body.String())
		}
		var decoded map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &decoded); err != nil {
			t.Fatal(err)
		}
		return decoded["result"].(map[string]any)
	}
	draft := call("h-user-id", "tools/call", map[string]any{"name": "dynamic_skill_submit", "arguments": map[string]any{"name": "userhub-pipeline", "description": "Monitor User Hub", "workflow": "Check CI and deployment.", "visibility": "personal"}})
	ref := draft["structuredContent"].(map[string]any)["skillRef"].(string)
	if _, err := s.review(request{SkillRef: ref, Subject: "reviewer", Publish: true}); err != nil {
		t.Fatal(err)
	}
	search := func(subject string) []any {
		result := call(subject, "tools/call", map[string]any{"name": "dynamic_skill_search", "arguments": map[string]any{"query": "userhub pipeline"}})
		return result["structuredContent"].(map[string]any)["matches"].([]any)
	}
	if len(search("h-user-id")) != 1 || len(search("other-user")) != 0 {
		t.Fatal("personal skill visibility does not follow authenticated subject")
	}
	loaded := call("other-user", "tools/call", map[string]any{"name": "dynamic_skill_load", "arguments": map[string]any{"skillRef": ref, "instruction": "Monitor"}})
	if loaded["isError"] != true {
		t.Fatalf("another user loaded a personal workflow: %v", loaded)
	}
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
	if status, body := call("backend-secret", "", "tools/call", map[string]any{"name": "tenant_access_status", "arguments": map[string]any{}}); status != 200 || body["result"].(map[string]any)["isError"] != true {
		t.Fatalf("ToolHive service credential called user status: %d %v", status, body)
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

func TestMCPVerifiedBearerWithoutTenantCanStartWithNoTenantTools(t *testing.T) {
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
	for _, method := range []string{"initialize", "tools/list", "tools/call"} {
		params := `{}`
		if method == "tools/call" {
			params = `{"name":"tenant_access_status","arguments":{}}`
		}
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"`+method+`","params":`+params+`}`))
		req.Header.Set("Authorization", "Bearer "+fixtureToken())
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != http.StatusOK || response.Header().Get("WWW-Authenticate") != "" {
			t.Fatalf("verified user without a tenant could not %s: %d %s", method, response.Code, response.Body.String())
		}
		if method == "tools/list" && (!strings.Contains(response.Body.String(), "tenant_access_status") || strings.Contains(response.Body.String(), "tenant_claim_create")) {
			t.Fatalf("unassigned user saw tenant tools: %s", response.Body.String())
		}
		if method == "tools/call" && (!strings.Contains(response.Body.String(), `"tenantAuthorized":false`) ||
			!strings.Contains(response.Body.String(), `"status":"personal_provisioning"`) ||
			!strings.Contains(response.Body.String(), `"recoveryAction":"sign in again to provision or restore the Personal organization"`)) {
			t.Fatalf("unassigned status did not report the boundary: %s", response.Body.String())
		}
	}
}

func TestMCPUnavailableTenantAuthorizerReturnsServiceUnavailable(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "unavailable"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`))
	req.Header.Set("Authorization", "Bearer "+fixtureToken())
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusServiceUnavailable || response.Header().Get("WWW-Authenticate") != "" {
		t.Fatalf("tenant authorizer outage was mistaken for missing access: %d %s", response.Code, response.Body.String())
	}
}

func TestMCPSharedPersonalClassRetainsOwnerAndPublishesResourceTools(t *testing.T) {
	s := testServer(t)
	owner := "personal-11111111-1111-4111-8111-111111111111"
	other := "personal-22222222-2222-4222-8222-222222222222"
	for _, item := range []struct{ tenant, name string }{{owner, "own-workflow"}, {other, "other-workflow"}} {
		if _, err := s.submit(request{Tenant: "admin", TargetTenant: item.tenant, Subject: "reviewer", Name: item.name, Description: "needle workflow", Workflow: "Use approved tools."}, true); err != nil {
			t.Fatal(err)
		}
	}
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/capabilities" {
			writeJSON(w, 200, map[string]any{"tenantRef": owner, "grants": []any{map[string]any{"name": "personal-harbor", "consumableRef": "registry.harbor.v1", "roleRef": "operator", "lifecycle": "Active"}}})
			return
		}
		writeJSON(w, 200, map[string]string{
			"tenantRef":        "personal-11111111-1111-4111-8111-111111111111",
			"runtimeTenantRef": "personal-standard", "role": "admin", "defaultNamespace": owner + "-default",
		})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	for _, test := range []struct{ method, params string }{
		{"initialize", `{}`},
		{"tools/list", `{}`},
		{"tools/call", `{"name":"tenant_access_status","arguments":{}}`},
		{"tools/call", `{"name":"service_catalog_list","arguments":{}}`},
		{"tools/call", `{"name":"re8ch_review_demo.service_catalog_list","arguments":{}}`},
		{"tools/call", `{"name":"dynamic_skill_search","arguments":{"query":"needle"}}`},
	} {
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"`+test.method+`","params":`+test.params+`}`))
		req.Header.Set("Authorization", "Bearer "+fixtureToken())
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != http.StatusOK {
			t.Fatalf("shared personal %s returned %d: %s", test.method, response.Code, response.Body.String())
		}
		if test.method == "tools/list" {
			var listed struct {
				Result struct {
					Tools []struct {
						Name string `json:"name"`
					} `json:"tools"`
				} `json:"result"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &listed); err != nil {
				t.Fatal(err)
			}
			names := map[string]bool{}
			for _, item := range listed.Result.Tools {
				names[item.Name] = true
			}
			for _, name := range []string{"tenant_access_status", "service_catalog_list", "service_instance_create", "service_binding_list", "usage_summary_get", "tenant_claim_create", "tenant_kubernetes_get", "runtime_image_catalog", "runtime_image_manage", "runtime_image_build", "monitoring_catalog", "monitoring_query", "monitoring_analyze", "monitoring_dashboard", "byoc_cluster_register", "dynamic_skill_search", "dynamic_skill_load", "dynamic_skill_submit"} {
				if !names[name] {
					t.Fatalf("shared class omitted resource tool %s", name)
				}
			}
			for _, name := range []string{"grafana_capability_list", "grafana_query", "grafana_exploration_delete", "harbor_project_get", "harbor_tag_delete", "harbor_build_start"} {
				if names[name] {
					t.Fatalf("compatibility alias %s leaked into tools/list", name)
				}
			}
		}
		if strings.Contains(test.params, "tenant_access_status") && (!strings.Contains(response.Body.String(), `"organizationRef":"personal-11111111-1111-4111-8111-111111111111"`) || !strings.Contains(response.Body.String(), `"runtimeTenantRef":"personal-standard"`) || !strings.Contains(response.Body.String(), `"tenantAuthorized":true`) || !strings.Contains(response.Body.String(), `"resourceOperationsAvailable":true`)) {
			t.Fatalf("shared class status lost owner or access gate: %s", response.Body.String())
		}
		if strings.Contains(test.params, "service_catalog_list") && !strings.Contains(response.Body.String(), `"tenantRef":"`+owner+`"`) {
			t.Fatalf("shared catalog lost logical owner: %s", response.Body.String())
		}
		if strings.Contains(test.params, "dynamic_skill_search") && (!strings.Contains(response.Body.String(), "own-workflow") || strings.Contains(response.Body.String(), "other-workflow")) {
			t.Fatalf("shared skill search leaked another organization: %s", response.Body.String())
		}
	}
}

func TestMCPSharedPersonalResourcesStayWithVerifiedOwner(t *testing.T) {
	s := testServer(t)
	var err error
	s.ledger, err = openLedger(t.TempDir() + "/ledger.db")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.ledger.Close() })
	first := "personal-11111111-1111-4111-8111-111111111111"
	second := "personal-22222222-2222-4222-8222-222222222222"
	spec := map[string]any{"serviceClass": "database.postgresql.shared.v1", "planRef": "standard", "regionRef": "loudi"}
	firstOperation, firstBinding := "", ""
	for _, item := range []struct{ owner, name string }{{first, "first-db"}, {second, "second-db"}} {
		operation, err := s.instanceMutate(item.owner, item.name, spec, "create-"+item.name, "create")
		if err != nil {
			t.Fatal(err)
		}
		binding, err := s.bindingCreate(item.owner, "service://"+item.owner+"/"+item.name, "endpoint", nil)
		if err != nil {
			t.Fatal(err)
		}
		if item.owner == first {
			firstOperation = operation["operationId"].(string)
			firstBinding = binding["bindingRef"].(string)
		}
	}
	if _, err := s.instanceGet(second, "service://"+first+"/first-db"); err == nil {
		t.Fatal("second Personal owner read first owner's instance")
	}
	if _, err := s.operationGet(second, firstOperation); err == nil {
		t.Fatal("second Personal owner replayed first owner's operation")
	}
	if _, err := s.bindingRevoke(second, firstBinding); err == nil {
		t.Fatal("second Personal owner revoked first owner's binding")
	}
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, err := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if err != nil {
			w.WriteHeader(401)
			return
		}
		writeJSON(w, 200, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/resolve-supabase" {
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			claims, _ := decodeClaims(body["accessToken"])
			owner := first
			if claims.Subject == "user-2" {
				owner = second
			}
			writeJSON(w, 200, map[string]string{"tenantRef": owner, "runtimeTenantRef": "personal-standard", "role": "admin", "defaultNamespace": owner + "-default"})
			return
		}
		if r.URL.Path == "/v1/services" {
			writeJSON(w, 200, map[string]any{"items": []any{}})
			return
		}
		w.WriteHeader(404)
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	for _, item := range []struct{ subject, own, other string }{{"user-1", "first-db", "second-db"}, {"user-2", "second-db", "first-db"}} {
		for _, tool := range []string{"service_instance_list", "service_binding_list"} {
			request := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"`+tool+`","arguments":{}}}`))
			request.Header.Set("Authorization", "Bearer "+fixtureTokenFor(item.subject))
			request.Header.Set("Content-Type", "application/json")
			request.Header.Set("Accept", "application/json, text/event-stream")
			response := httptest.NewRecorder()
			s.handler().ServeHTTP(response, request)
			if response.Code != 200 || !strings.Contains(response.Body.String(), item.own) || strings.Contains(response.Body.String(), item.other) {
				t.Fatalf("%s crossed owner boundary for %s: %d %s", tool, item.subject, response.Code, response.Body.String())
			}
		}
	}
}

func TestMCPSharedPersonalViewerCannotMutateResourceFamilies(t *testing.T) {
	s := testServer(t)
	owner := "personal-11111111-1111-4111-8111-111111111111"
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"tenantRef": owner, "runtimeTenantRef": "personal-standard", "role": "viewer", "defaultNamespace": owner + "-default"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	for _, item := range []struct{ name, args string }{
		{"tenant_claim_create", `{"claim":{},"claim_hash":"hash"}`},
		{"byoc_cluster_register", `{"cluster_uid":"cluster","region_ref":"loudi","issuer":"https://example.com","public_key":"key"}`},
		{"harbor_tag_delete", `{"credential_ref":"credential://harbor/owner/ref","repository":"repo","reference":"sha256:abc","tag":"tag"}`},
		{"monitoring_dashboard", `{"action":"delete","dashboard_uid":"dashboard"}`},
	} {
		request := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"`+item.name+`","arguments":`+item.args+`}}`))
		request.Header.Set("Authorization", "Bearer "+fixtureToken())
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, request)
		if response.Code != 200 || !strings.Contains(response.Body.String(), "operator tenant role is required") {
			t.Fatalf("viewer reached %s: %d %s", item.name, response.Code, response.Body.String())
		}
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
				Name        string         `json:"name"`
				Annotations map[string]any `json:"annotations"`
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
		for _, key := range []string{"readOnlyHint", "destructiveHint", "openWorldHint"} {
			if _, ok := item.Annotations[key].(bool); !ok {
				t.Errorf("published tool %s is missing explicit boolean %s", item.Name, key)
			}
		}
	}
	hiddenAliases := 0
	for name := range legacy {
		if strings.HasPrefix(name, "artc_") {
			if names[name] {
				t.Errorf("tenant-specific tool %s leaked into generic catalog", name)
			}
			continue
		}
		if alias, ok := toolAliases[name]; ok {
			hiddenAliases++
			if names[name] {
				t.Errorf("compatibility alias %s leaked into generic catalog", name)
			}
			if !names[alias.Canonical] {
				t.Errorf("compatibility alias %s has unpublished canonical tool %s", name, alias.Canonical)
			}
			continue
		}
		if !names[name] {
			t.Errorf("missing tenant tool %s", name)
		}
	}
	for _, name := range []string{"tenant_access_status", "tenant_organization_context_list", "tenant_organization_context_select", "tenant_self_service_capabilities", "dynamic_skill_search", "dynamic_skill_load", "dynamic_skill_submit"} {
		if !names[name] {
			t.Errorf("missing dynamic skill tool %s", name)
		}
	}
	if len(names) != len(legacy)-hiddenAliases+4 {
		t.Fatalf("published %d tools, expected %d", len(names), len(legacy)-hiddenAliases+4)
	}
}

func TestArtcToolsOnlyPublishedToArtchaisTenant(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var selected struct {
			Tenant string `json:"tenantRef"`
		}
		if err := json.NewDecoder(r.Body).Decode(&selected); err != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid request"})
			return
		}
		writeJSON(w, 200, map[string]string{"tenantRef": selected.Tenant, "role": "viewer", "defaultNamespace": selected.Tenant})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("ARTCHAIS_TENANT_REF", "artchais")
	for _, tenant := range []string{"openai-review-demo", "artchais"} {
		request := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}`))
		request.Header.Set("Authorization", "Bearer "+fixtureToken())
		request.Header.Set("X-RE8CH-Tenant", tenant)
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, request)
		if response.Code != 200 {
			t.Fatalf("%s tools/list status %d: %s", tenant, response.Code, response.Body.String())
		}
		var listed struct {
			Result struct {
				Tools []struct {
					Name string `json:"name"`
				} `json:"tools"`
			} `json:"result"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &listed); err != nil {
			t.Fatal(err)
		}
		artcCount := 0
		for _, tool := range listed.Result.Tools {
			if strings.HasPrefix(tool.Name, "artc_") {
				artcCount++
			}
		}
		if want := map[bool]int{true: 3, false: 0}[tenant == "artchais"]; artcCount != want {
			t.Errorf("%s published %d artc tools, want %d", tenant, artcCount, want)
		}
	}
}

func TestReviewDemoBatchToolNamesUseCanonicalHandlers(t *testing.T) {
	body := `[{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"re8ch_review_demo.service_catalog_list","arguments":{}}}]`
	request := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(body))
	if err := rewriteReviewDemoToolCall(request); err != nil {
		t.Fatal(err)
	}
	normalized, err := io.ReadAll(request.Body)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(normalized), legacyToolPrefixes[0]) || !strings.Contains(string(normalized), `"name":"service_catalog_list"`) {
		t.Fatalf("namespaced batch tool call was not normalized: %s", normalized)
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
	capabilities := object(object(result["result"])["capabilities"])
	tools := object(capabilities["tools"])
	if listChanged, _ := tools["listChanged"].(bool); !listChanged {
		t.Fatalf("dynamic tool refresh is not advertised: %v", result)
	}
	request = httptest.NewRequest("GET", "/.well-known/oauth-protected-resource/mcp", nil)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 || !strings.Contains(response.Body.String(), supabaseIssuer) {
		t.Fatalf("OAuth metadata failed: %d %s", response.Code, response.Body.String())
	}
}
