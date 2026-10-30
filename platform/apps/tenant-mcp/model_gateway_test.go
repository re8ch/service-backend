package main

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func modelGatewayFixture(t *testing.T) {
	t.Helper()
	t.Setenv("LITELLM_TENANT_BINDINGS_JSON", `{"re8ch":{"tokenEnv":"LITELLM_TENANT_RE8CH_KEY","models":["research-model"]}}`)
	t.Setenv("LITELLM_TENANT_RE8CH_KEY", "test-tenant-key")
	t.Setenv("LITELLM_GATEWAY_URL", "https://gateway.invalid/v1")
}

func TestModelGatewayBindingIsolation(t *testing.T) {
	modelGatewayFixture(t)
	if !modelGatewayConfigured("re8ch") {
		t.Fatal("expected configured binding")
	}
	for _, tenant := range []string{"admin", "other", "personal-standard"} {
		if _, err := tenantModelGatewayBinding(tenant); err == nil {
			t.Fatalf("cross-tenant binding accepted: %s", tenant)
		}
	}
	t.Setenv("LITELLM_TENANT_BINDINGS_JSON", `{"re8ch":{"tokenEnv":"LITELLM_MASTER_KEY","models":["research-model"]}}`)
	if _, err := tenantModelGatewayBinding("re8ch"); err == nil {
		t.Fatal("master key reference accepted")
	}
	t.Setenv("LITELLM_TENANT_BINDINGS_JSON", `{"re8ch":{"tokenEnv":"LITELLM_TENANT_RE8CH_KEY","models":["research-model"]},"other":{"tokenEnv":"LITELLM_TENANT_RE8CH_KEY","models":["research-model"]}}`)
	if _, err := tenantModelGatewayBinding("re8ch"); err == nil {
		t.Fatal("shared credential reference accepted")
	}
}

func TestModelGatewayGrantIsRequiredBeforeCredentials(t *testing.T) {
	modelGatewayFixture(t)
	calls := 0
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("X-Tenant-Ref") != "re8ch" {
			t.Error("unexpected caller scope")
		}
		writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{}})
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "test-control")
	s := &server{}
	for _, id := range []identity{{Subject: "user", Tenant: "re8ch", Role: "operator"}, {Subject: "service", Tenant: "re8ch", Role: "admin", Service: true}} {
		if _, err := s.callModelGateway(context.Background(), id, "model_gateway_catalog", map[string]any{}); err == nil {
			t.Fatal("unauthorized request accepted")
		}
	}
	if calls != 1 {
		t.Fatalf("service token reached grant backend: %d", calls)
	}
}

func TestModelGatewayPayloadRejectsEscapesAndLimits(t *testing.T) {
	binding := modelGatewayBinding{Models: []string{"research-model"}}
	valid := func() map[string]any {
		return map[string]any{"model": "research-model", "messages": []any{map[string]any{"role": "user", "content": "Synthetic task"}}}
	}
	for _, mutate := range []func(map[string]any){
		func(a map[string]any) { a["tenant"] = "other" },
		func(a map[string]any) { a["api_key"] = "override" },
		func(a map[string]any) { a["model"] = "unapproved" },
		func(a map[string]any) { a["max_output_tokens"] = float64(2049) },
		func(a map[string]any) { a["max_output_tokens"] = float64(1.5) },
		func(a map[string]any) { a["messages"] = []any{map[string]any{"role": "tool", "content": "bad"}} },
		func(a map[string]any) {
			a["messages"] = []any{map[string]any{"role": "user", "content": strings.Repeat("x", 32769)}}
		},
		func(a map[string]any) { a["messages"] = []any{} },
	} {
		args := valid()
		mutate(args)
		if _, err := modelGatewayPayload(args, binding); err == nil {
			t.Fatal("invalid payload accepted")
		}
	}
	payload, err := modelGatewayPayload(valid(), binding)
	if err != nil || payload["max_tokens"] != 1200 || payload["stream"] != false || payload["n"] != 1 {
		t.Fatalf("unexpected bounded payload: %v %v", payload, err)
	}
}

func TestModelGatewayTransportDoesNotLeakOrRedirect(t *testing.T) {
	forwarded := 0
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { forwarded++ }))
	defer destination.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer sensitive-test-key" {
			t.Error("missing tenant authorization")
		}
		if r.URL.Path == "/redirect" {
			http.Redirect(w, r, destination.URL, 302)
			return
		}
		if r.URL.Path == "/large" {
			fmt.Fprint(w, strings.Repeat("x", modelGatewayMaxResponse+1))
			return
		}
		if r.URL.Path == "/slow" {
			time.Sleep(30 * time.Millisecond)
			return
		}
		http.Error(w, "sensitive-test-key upstream debugging", 403)
	}))
	defer upstream.Close()
	client := &modelGatewayClient{base: upstream.URL, token: "sensitive-test-key", http: &http.Client{Timeout: 10 * time.Millisecond, CheckRedirect: func(*http.Request, []*http.Request) error { return fmt.Errorf("redirect denied") }}}
	for _, path := range []string{"/redirect", "/error", "/large", "/slow"} {
		_, err := client.request(context.Background(), "GET", path, nil)
		if err == nil || strings.Contains(err.Error(), "sensitive-test-key") || strings.Contains(err.Error(), upstream.URL) {
			t.Fatalf("unsafe error for %s: %v", path, err)
		}
	}
	if forwarded != 0 {
		t.Fatal("authorization redirected")
	}
}

func TestModelGatewayCompletionRejectsTruncationAndFiltersMetadata(t *testing.T) {
	result := map[string]any{"model": "resolved-model", "choices": []any{map[string]any{"finish_reason": "length", "message": map[string]any{"content": "partial"}}}, "api_key": "must-not-return", "usage": map[string]any{"prompt_tokens": float64(10), "cost": "must-not-return"}}
	if _, err := modelGatewayCompletion(result); err == nil {
		t.Fatal("truncated output accepted")
	}
	object(slice(result["choices"])[0])["finish_reason"] = "stop"
	value, err := modelGatewayCompletion(result)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(value)
	if strings.Contains(string(raw), "must-not-return") || value["browserExecuted"] != false || value["model"] != "resolved-model" {
		t.Fatalf("unsafe completion: %s", raw)
	}
}

func TestModelGatewaySDKIntegerArgument(t *testing.T) {
	ctx := context.Background()
	ms := mcp.NewServer(&mcp.Implementation{Name: "test", Version: "1"}, nil)
	definitions, err := legacyDefinitions()
	if err != nil {
		t.Fatal(err)
	}
	definition := definitions["model_gateway_chat"]
	mcp.AddTool(ms, &mcp.Tool{Name: definition.Name, InputSchema: definition.InputSchema}, func(ctx context.Context, req *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
		payload, err := modelGatewayPayload(args, modelGatewayBinding{Models: []string{"research-model"}})
		return nil, payload, err
	})
	left, right := mcp.NewInMemoryTransports()
	ss, err := ms.Connect(ctx, left, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ss.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "1"}, nil)
	session, err := client.Connect(ctx, right, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "model_gateway_chat", Arguments: map[string]any{"model": "research-model", "messages": []any{map[string]any{"role": "user", "content": "Synthetic task"}}, "max_output_tokens": 512}})
	if err != nil || result.IsError {
		t.Fatalf("SDK integer was rejected: %v %v", result, err)
	}
}

func TestModelGatewayAuthenticatedSDKCallsUseTenantKeyOnce(t *testing.T) {
	modelGatewayFixture(t)
	s := testServer(t)
	posts := 0
	gateway := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-tenant-key" {
			t.Error("wrong tenant credential")
		}
		if r.URL.Path == "/v1/models" {
			writeJSON(w, 200, map[string]any{"data": []any{map[string]any{"id": "research-model"}, map[string]any{"id": "not-approved"}}, "private_config": "must-not-return"})
			return
		}
		if r.URL.Path != "/v1/chat/completions" || r.Method != "POST" {
			t.Errorf("unexpected gateway route: %s", r.URL.Path)
		}
		posts++
		var payload map[string]any
		if json.NewDecoder(r.Body).Decode(&payload) != nil || payload["max_tokens"] != float64(512) || payload["stream"] != false {
			t.Error("unbounded SDK payload")
		}
		writeJSON(w, 200, map[string]any{"model": "resolved-model", "choices": []any{map[string]any{"finish_reason": "stop", "message": map[string]any{"role": "assistant", "content": "Synthetic finding"}}}})
	}))
	defer gateway.Close()
	// Trust only this test server's certificate. Production clients still use
	// normal system roots; no TLS verification bypass is added to runtime code.
	oldTransport := http.DefaultTransport
	http.DefaultTransport = gateway.Client().Transport
	defer func() { http.DefaultTransport = oldTransport }()
	t.Setenv("LITELLM_GATEWAY_URL", gateway.URL)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"id": "user-1"}) }))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/capabilities" {
			writeJSON(w, 200, map[string]any{"tenantRef": "re8ch", "grants": []any{map[string]any{"consumableRef": modelGatewayCapability, "roleRef": "operator", "lifecycle": "Active"}}})
			return
		}
		writeJSON(w, 200, map[string]string{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "test-control")
	for _, endpoint := range []string{"/mcp", "/self-service/mcp"} {
		for _, params := range []string{`{"name":"model_gateway_catalog","arguments":{}}`, `{"name":"model_gateway_chat","arguments":{"model":"research-model","messages":[{"role":"user","content":"Synthetic task"}],"max_output_tokens":512}}`} {
			req := httptest.NewRequest("POST", endpoint, strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":`+params+`}`))
			req.Header.Set("Authorization", "Bearer "+fixtureToken())
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Accept", "application/json, text/event-stream")
			recorder := httptest.NewRecorder()
			s.handler().ServeHTTP(recorder, req)
			body := recorder.Body.String()
			if recorder.Code != 200 || strings.Contains(body, `"isError":true`) || strings.Contains(body, "must-not-return") || strings.Contains(body, "not-approved") || strings.Contains(body, "test-tenant-key") {
				t.Fatalf("unsafe SDK result: %d %s", recorder.Code, body)
			}
			if !strings.Contains(body, "research-model") && !strings.Contains(body, "Synthetic finding") {
				t.Fatalf("missing completion/catalog: %s", body)
			}
		}
	}
	if posts != 2 {
		t.Fatalf("expected one POST per call, got %d", posts)
	}
}
