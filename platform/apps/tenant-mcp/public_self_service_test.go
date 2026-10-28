package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPublicSelfServiceEndpointPublishesOnlyFourCapabilities(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "test-only")
	call := func(path, method, params string) (int, string) {
		t.Helper()
		req := httptest.NewRequest("POST", path, strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"`+method+`","params":`+params+`}`))
		req.Header.Set("Authorization", "Bearer "+fixtureToken())
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		return response.Code, response.Body.String()
	}
	status, body := call("/self-service/mcp", "tools/list", `{}`)
	if status != 200 {
		t.Fatalf("tools/list status %d: %s", status, body)
	}
	var listed struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(body), &listed); err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, tool := range listed.Result.Tools {
		names[tool.Name] = true
	}
	for _, name := range []string{"tenant_namespace_list", "tenant_claim_plan", "runtime_image_catalog", "monitoring_query"} {
		if !names[name] {
			t.Errorf("missing public tool %s", name)
		}
	}
	for _, name := range []string{"service_catalog_list", "byoc_cluster_register", "platform_tool_catalog", "tenant_human_access_issue", "tenant_kubernetes_apply", "dynamic_skill_search", "tenant_capabilities_list"} {
		if names[name] {
			t.Errorf("unexpected public tool %s", name)
		}
	}
	if len(names) != 17 {
		t.Errorf("unexpected public tool count: %d", len(names))
	}
	if status, body = call("/self-service/mcp", "tools/call", `{"name":"byoc_cluster_register","arguments":{}}`); status != 200 || !strings.Contains(body, "unknown tool") {
		t.Fatalf("hidden tool was callable: status=%d body=%s", status, body)
	}
	if status, body = call("/self-service/mcp", "tools/call", `{"name":"tenant_claim_plan","arguments":{"claim":{"spec":{"capability":"storage.volume.v1"}}}}`); status != 200 || !strings.Contains(body, "only workload claims") {
		t.Fatalf("storage claim passed public contract: status=%d body=%s", status, body)
	}
	if status, body = call("/mcp", "tools/list", `{}`); status != 200 || !strings.Contains(body, "service_catalog_list") {
		t.Fatalf("legacy endpoint changed: status=%d body=%s", status, body)
	}
	metadata := httptest.NewRecorder()
	s.handler().ServeHTTP(metadata, httptest.NewRequest("GET", "/.well-known/oauth-protected-resource/tenant-self-service/mcp", nil))
	if metadata.Code != 200 || !strings.Contains(metadata.Body.String(), `"resource":"https://tools.service.re8ch.com/tenant-self-service/mcp"`) {
		t.Fatalf("public resource metadata: status=%d body=%s", metadata.Code, metadata.Body.String())
	}
}
