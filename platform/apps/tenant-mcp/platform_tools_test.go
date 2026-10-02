package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestPlatformAdminMembershipIsLiveAndFailClosed(t *testing.T) {
	active := true
	rpc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer subject-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if active {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "admin", "tenant_role": "admin"}})
		} else {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "re8ch", "tenant_role": "admin"}})
		}
	}))
	defer rpc.Close()
	t.Setenv("PLATFORM_MEMBERSHIP_URL", rpc.URL)
	allowed, err := currentPlatformAdmin(context.Background(), "subject-token")
	if err != nil || !allowed {
		t.Fatalf("active platform admin denied: %v", err)
	}
	active = false
	allowed, err = currentPlatformAdmin(context.Background(), "subject-token")
	if err != nil || allowed {
		t.Fatalf("revoked platform admin allowed: %v", err)
	}
	allowed, err = currentPlatformAdmin(context.Background(), "other-token")
	if err == nil || allowed {
		t.Fatal("unauthorized RPC call allowed")
	}
}

func TestPlatformBackendMCPProxyPreservesAuthAndResult(t *testing.T) {
	backend := mcp.NewServer(&mcp.Implementation{Name: "fixture-admin", Version: "1.0.0"}, nil)
	mcp.AddTool(backend, &mcp.Tool{Name: "tenant_list", Description: "List tenants"},
		func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			return nil, map[string]any{"tenants": []string{"re8ch"}}, nil
		})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-service-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return backend }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}).ServeHTTP(w, r)
	}))
	defer upstream.Close()
	t.Setenv("SERVICE_API_TOKEN", "fixture-service-token")
	t.Setenv("PLATFORM_TENANT_ADMIN_MCP_URL", upstream.URL)
	value, err := withPlatformBackend(context.Background(), "tenant_admin", func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
		if !allowed["tenant_list"] || allowed["unexpected_tool"] {
			t.Fatal("backend tool allowlist changed")
		}
		return session.CallTool(context.Background(), &mcp.CallToolParams{Name: "tenant_list", Arguments: map[string]any{}})
	})
	if err != nil {
		t.Fatal(err)
	}
	result := value.(*mcp.CallToolResult)
	if result.IsError || result.StructuredContent == nil {
		t.Fatalf("upstream result lost: %#v", result)
	}
}

func TestPlatformToolDiscoveryAndGuessedCallAreIdentityScoped(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, _ := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		writeJSON(w, 200, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"tenantRef": "re8ch", "role": "operator", "defaultNamespace": "re8ch"})
	}))
	defer control.Close()
	active := true
	rpcCalls := 0
	rpc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rpcCalls++
		claims, _ := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if active && claims.Subject == "admin-user" {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "admin", "tenant_role": "admin"}})
			return
		}
		writeJSON(w, 200, []map[string]string{})
	}))
	defer rpc.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("PLATFORM_MEMBERSHIP_URL", rpc.URL)
	backend := mcp.NewServer(&mcp.Implementation{Name: "fixture-admin", Version: "1.0.0"}, nil)
	mcp.AddTool(backend, &mcp.Tool{Name: "tenant_list"}, func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
		return nil, map[string]any{"tenants": []string{"re8ch"}}, nil
	})
	upstream := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return backend }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}))
	defer upstream.Close()
	t.Setenv("SERVICE_API_TOKEN", "fixture-service-token")
	t.Setenv("PLATFORM_TENANT_ADMIN_MCP_URL", upstream.URL)
	call := func(subject, session, method string, params any) map[string]any {
		claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": subject, "session_id": session, "exp": time.Now().Add(time.Hour).Unix()})
		token := "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != 200 {
			t.Fatalf("MCP HTTP %d: %s", response.Code, response.Body.String())
		}
		var result map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	contains := func(result map[string]any) bool {
		tools := result["result"].(map[string]any)["tools"].([]any)
		for _, item := range tools {
			if item.(map[string]any)["name"] == "platform_tool_call" {
				return true
			}
		}
		return false
	}
	if !contains(call("admin-user", "session-1", "tools/list", map[string]any{})) {
		t.Fatal("admin tool absent")
	}
	if rpcCalls != 1 {
		t.Fatalf("first session made %d membership calls", rpcCalls)
	}
	privileged := call("admin-user", "session-1", "tools/call", map[string]any{"name": "platform_tool_call", "arguments": map[string]any{"backend": "tenant_admin", "toolName": "tenant_list"}})
	if privileged["error"] != nil || privileged["result"].(map[string]any)["isError"] == true {
		t.Fatalf("admin call without AAL2 failed: %#v", privileged)
	}
	if rpcCalls != 1 {
		t.Fatalf("same-session call repeated membership lookup: %d", rpcCalls)
	}
	if contains(call("ordinary-user", "session-2", "tools/list", map[string]any{})) {
		t.Fatal("ordinary user sees admin tool")
	}
	guessed := call("ordinary-user", "session-2", "tools/call", map[string]any{"name": "platform_tool_call", "arguments": map[string]any{"backend": "tenant_admin", "toolName": "tenant_list"}})
	if guessed["error"] == nil {
		t.Fatal("guessed privileged call was accepted")
	}
	if rpcCalls != 2 {
		t.Fatalf("ordinary session repeated membership lookup: %d", rpcCalls)
	}
	active = false
	if !contains(call("admin-user", "session-1", "tools/list", map[string]any{})) || rpcCalls != 2 {
		t.Fatal("existing login session lost its cached admin decision")
	}
	if contains(call("admin-user", "session-3", "tools/list", map[string]any{})) || rpcCalls != 3 {
		t.Fatal("new login session did not receive a fresh membership decision")
	}
}
