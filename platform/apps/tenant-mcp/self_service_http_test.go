package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type authenticatedMCPTransport struct {
	token string
	base  http.RoundTripper
}

func (t authenticatedMCPTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	clone := request.Clone(request.Context())
	clone.Header = request.Header.Clone()
	clone.Header.Set("Authorization", "Bearer "+t.token)
	clone.Header.Set("X-RE8CH-Tenant", "re8ch")
	return t.base.RoundTrip(clone)
}

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
				map[string]any{"spec": map[string]any{"tenantRef": "re8ch", "operation": "EnsurePresent", "namespaceName": "re8ch-expired", "expiresAt": "2020-01-01T00:00:00Z"}, "status": map[string]any{"phase": "Ready"}},
				map[string]any{"spec": map[string]any{"tenantRef": "other", "operation": "EnsurePresent", "namespaceName": "other-default"}, "status": map[string]any{"phase": "Ready"}},
			}})
			return
		}
		if r.URL.Path == "/v1/capabilities" {
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{
				map[string]any{"name": "re8ch-storage", "consumableRef": "storage.volume.v1", "roleRef": "operator", "lifecycle": "Active"},
				map[string]any{"name": "re8ch-sandbox", "consumableRef": "sandbox.runtime.v1", "roleRef": "admin", "lifecycle": "Suspended"},
				map[string]any{"name": "re8ch-network", "consumableRef": "network.tenant.v1", "roleRef": "operator", "lifecycle": "Active"},
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
	if result["tenantRef"] != "re8ch" || !strings.Contains(w.Body.String(), "re8ch-default") || strings.Contains(w.Body.String(), "re8ch-expired") || strings.Contains(w.Body.String(), "other-default") || !strings.Contains(w.Body.String(), "exposure") {
		t.Fatalf("unexpected scope: %s", w.Body.String())
	}
	legacy, err := s.callClaim(context.Background(), identity{Tenant: "re8ch", Role: "operator"}, "tenant_capabilities_list", map[string]any{})
	if err != nil {
		t.Fatalf("legacy discovery failed: %v", err)
	}
	legacyResult := legacy.(map[string]any)
	legacySelfService := legacyResult["selfService"].(map[string]any)
	if legacyResult["selfServiceStatus"] != "ready" || len(legacySelfService["readyNamespaces"].([]string)) != 1 || legacySelfService["readyNamespaces"].([]string)[0] != "re8ch-default" {
		t.Fatalf("legacy discovery diverged from executable namespace scope: %v", legacyResult)
	}
	grants := result["grantedConsumables"].([]any)
	if len(grants) != 3 || grants[0].(map[string]any)["canRequest"] != true || grants[1].(map[string]any)["canRequest"] != false || grants[2].(map[string]any)["canRequest"] != true {
		t.Fatalf("unexpected grant projection: %v", grants)
	}
	for _, raw := range result["capabilities"].([]any) {
		capability := raw.(map[string]any)
		if capability["id"] == "storage" && (capability["canWrite"] != true || capability["requiredConsumable"] != "storage.volume.v1") {
			t.Fatalf("PVC discovery does not match active storage grant: %v", capability)
		}
		if capability["id"] == "exposure" && (capability["canWrite"] != true || capability["requiredConsumable"] != "network.tenant.v1") {
			t.Fatalf("exposure discovery does not match active network grant: %v", capability)
		}
		if capability["id"] == "database-credential" && (capability["canRead"] != false || capability["databaseState"] != "unverified") {
			t.Fatalf("database credential discovery overstates ungranted availability: %v", capability)
		}
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

func TestPersonalCollaborationGrantDoesNotWidenInfrastructureRoles(t *testing.T) {
	viewerGrant := map[string]any{"consumableRef": "collaboration.stalwart.v1", "roleRef": "viewer", "lifecycle": "Active", "canRequest": false}
	operatorGrant := map[string]any{"consumableRef": "collaboration.stalwart.v1", "roleRef": "operator", "lifecycle": "Active", "canRequest": false}
	storageGrant := map[string]any{"consumableRef": "storage.volume.v1", "roleRef": "operator", "lifecycle": "Active", "canRequest": false}
	if !hasPersonalCollaborationGrant([]map[string]any{viewerGrant}, "viewer") {
		t.Fatal("viewer-specific personal consent grant was rejected")
	}
	if hasPersonalCollaborationGrant([]map[string]any{operatorGrant}, "viewer") || hasPersonalCollaborationGrant([]map[string]any{storageGrant}, "viewer") {
		t.Fatal("viewer gained operator or infrastructure grant")
	}
	viewerGrant["lifecycle"] = "Suspended"
	if hasPersonalCollaborationGrant([]map[string]any{viewerGrant}, "viewer") {
		t.Fatal("suspended personal grant was accepted")
	}
	viewerGrant["lifecycle"] = "Active"
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/capabilities" {
			t.Fatalf("unexpected control path: %s", r.URL.Path)
		}
		writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{viewerGrant, storageGrant}})
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	viewer := identity{Tenant: "re8ch", Subject: "viewer-subject", Role: "viewer"}
	if err := requirePersonalCollaborationGrant(context.Background(), viewer); err != nil {
		t.Fatalf("personal viewer consent rejected: %v", err)
	}
	if err := requireGrantedConsumable(context.Background(), viewer, "storage.volume.v1"); err == nil {
		t.Fatal("viewer gained infrastructure request access")
	}
	if err := requirePersonalCollaborationGrant(context.Background(), identity{Tenant: "re8ch", Subject: "viewer-subject", Role: "viewer", Service: true}); err == nil {
		t.Fatal("service identity gained personal consent access")
	}
	if err := requirePersonalCollaborationGrant(context.Background(), identity{Tenant: "re8ch", Role: "viewer"}); err == nil {
		t.Fatal("subjectless identity gained personal consent access")
	}
}

func TestCapabilityRefreshPublishesDynamicToolList(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/namespaces":
			writeJSON(w, 200, map[string]any{"items": []any{map[string]any{"spec": map[string]any{"tenantRef": "re8ch", "operation": "EnsurePresent", "namespaceName": "re8ch-default"}, "status": map[string]any{"phase": "Ready"}}}})
		case "/v1/capabilities":
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{map[string]any{"name": "collaboration", "consumableRef": "collaboration.stalwart.v1", "roleRef": "operator", "lifecycle": "Active"}}})
		default:
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch-default"})
		}
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("COLLABORATION_ENABLED", "true")

	httpServer := httptest.NewServer(s.handler())
	defer httpServer.Close()
	changed := make(chan struct{}, 1)
	client := mcp.NewClient(&mcp.Implementation{Name: "dynamic-tools-test", Version: "1"}, &mcp.ClientOptions{
		ToolListChangedHandler: func(context.Context, *mcp.ToolListChangedRequest) {
			select {
			case changed <- struct{}{}:
			default:
			}
		},
	})
	httpClient := &http.Client{Transport: authenticatedMCPTransport{token: fixtureToken(), base: http.DefaultTransport}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: httpServer.URL + "/mcp", HTTPClient: httpClient}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	if _, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "tenant_self_service_capabilities", Arguments: map[string]any{}}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-changed:
	case <-ctx.Done():
		t.Fatal("tools/list_changed was not delivered to the connected client")
	}
	listed, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, tool := range listed.Tools {
		found = found || tool.Name == "mail_query"
	}
	if !found {
		t.Fatal("refreshed tool list omitted mail_query")
	}
}

func TestExposureGrantCannotBeBypassedByOperatorRole(t *testing.T) {
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/capabilities" {
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{}})
			return
		}
		t.Fatalf("unexpected control request after denied grant: %s", r.URL.Path)
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	_, err := (&server{}).createExposure(httptest.NewRequest(http.MethodGet, "/", nil).Context(), identity{Tenant: "re8ch", Role: "operator"}, exposureCreateInput{Namespace: "re8ch-default", Name: "web", Service: "web", Port: 80, Apply: true})
	if err == nil || !strings.Contains(err.Error(), "active tenant grant") {
		t.Fatalf("operator without network grant was not denied before Kubernetes access: %v", err)
	}
	if hasRequestableGrant([]map[string]any{{"consumableRef": "network.tenant.v1", "canRequest": false}}, "network.tenant.v1") {
		t.Fatal("non-requestable network grant accepted")
	}
}
