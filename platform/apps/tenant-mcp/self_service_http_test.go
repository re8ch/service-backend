package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSelfServiceCapabilitiesAreBoundToSelectedOrganization(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/readyz" {
			writeJSON(w, 200, map[string]any{"ready": true})
			return
		}
		if r.URL.Path == "/v1/namespaces" {
			writeJSON(w, 200, map[string]any{"items": []any{
				map[string]any{"spec": map[string]any{"tenantRef": "re8ch", "operation": "EnsurePresent", "namespaceName": "re8ch-default"}, "status": map[string]any{"phase": "Ready"}},
				map[string]any{"spec": map[string]any{"tenantRef": "other", "operation": "EnsurePresent", "namespaceName": "other-default"}, "status": map[string]any{"phase": "Ready"}},
			}})
			return
		}
		if r.URL.Path == "/v1/capabilities" {
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{
				map[string]any{"name": "re8ch-storage", "consumableRef": "storage.volume.v1", "roleRef": "operator", "lifecycle": "Active"},
				map[string]any{"name": "re8ch-sandbox", "consumableRef": "sandbox.runtime.v1", "roleRef": "admin", "lifecycle": "Suspended"},
			}})
			return
		}
		writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	call := func(selected, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(http.MethodGet, "/self-service/capabilities", nil)
		r.Header.Set("X-RE8CH-Tenant", selected)
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		s.handler().ServeHTTP(w, r)
		return w
	}
	if w := call("re8ch", "invalid"); w.Code != 401 {
		t.Fatalf("invalid token: %d %s", w.Code, w.Body.String())
	}
	w := call("re8ch", fixtureToken())
	if w.Code != 200 {
		t.Fatalf("capability discovery: %d %s", w.Code, w.Body.String())
	}
	var result map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result["tenantRef"] != "re8ch" || !strings.Contains(w.Body.String(), "re8ch-default") || strings.Contains(w.Body.String(), "other-default") || !strings.Contains(w.Body.String(), "exposure") {
		t.Fatalf("unexpected scope: %s", w.Body.String())
	}
	grants := result["grantedConsumables"].([]any)
	if len(grants) != 2 || grants[0].(map[string]any)["canRequest"] != true || grants[1].(map[string]any)["canRequest"] != false {
		t.Fatalf("unexpected grant projection: %v", grants)
	}
	if err := requireGrantedConsumable(httptest.NewRequest(http.MethodGet, "/", nil).Context(), identity{Tenant: "re8ch", Role: "operator"}, "storage.volume.v1"); err != nil {
		t.Fatalf("active operator grant rejected: %v", err)
	}
	if err := requireGrantedConsumable(httptest.NewRequest(http.MethodGet, "/", nil).Context(), identity{Tenant: "re8ch", Role: "viewer"}, "storage.volume.v1"); err == nil {
		t.Fatal("viewer was allowed to request an operator grant")
	}
	if err := requireGrantedConsumable(httptest.NewRequest(http.MethodGet, "/", nil).Context(), identity{Tenant: "re8ch", Role: "admin"}, "sandbox.runtime.v1"); err == nil {
		t.Fatal("suspended grant was allowed")
	}
	// Discovery must not omit a Kubernetes kind that the tenant apply broker accepts.
	discovered := map[string]bool{}
	for _, raw := range result["capabilities"].([]any) {
		for _, kind := range raw.(map[string]any)["kinds"].([]any) {
			discovered[kind.(string)] = true
		}
	}
	for gvk := range tenantApplyKinds {
		parts := strings.Split(gvk, "/")
		if !discovered[parts[len(parts)-1]] {
			t.Errorf("tenant apply kind %s omitted from self-service discovery", gvk)
		}
	}
	mcpRequest := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tenant_self_service_capabilities","arguments":{}}}`))
	mcpRequest.Header.Set("Authorization", "Bearer "+fixtureToken())
	mcpRequest.Header.Set("X-RE8CH-Tenant", "re8ch")
	mcpRequest.Header.Set("Content-Type", "application/json")
	mcpRequest.Header.Set("Accept", "application/json, text/event-stream")
	mcpResponse := httptest.NewRecorder()
	s.handler().ServeHTTP(mcpResponse, mcpRequest)
	if mcpResponse.Code != 200 || !strings.Contains(mcpResponse.Body.String(), `"tenantRef":"re8ch"`) || !strings.Contains(mcpResponse.Body.String(), "re8ch-default") || strings.Contains(mcpResponse.Body.String(), "other-default") || !strings.Contains(mcpResponse.Body.String(), "storage.volume.v1") {
		t.Fatalf("MCP and Hub discovery diverged: %d %s", mcpResponse.Code, mcpResponse.Body.String())
	}
}
