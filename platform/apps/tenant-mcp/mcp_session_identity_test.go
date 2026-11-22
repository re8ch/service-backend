package main

import (
	"context"
	"encoding/json"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestMCPSessionBindsVerifiedUserAndOrganizationButAllowsTokenRefresh(t *testing.T) {
	var calls atomic.Int32
	var last atomic.Value
	ms := mcp.NewServer(&mcp.Implementation{Name: "principal-fixture", Version: "1"}, nil)
	ms.AddReceivingMiddleware(verifiedMCPSessionIdentity)
	mcp.AddTool(ms, &mcp.Tool{Name: "read_fixture"}, func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
		calls.Add(1)
		last.Store(ctx.Value(identityKey{}).(identity))
		return nil, map[string]bool{"ok": true}, nil
	})
	transport := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return ms }, &mcp.StreamableHTTPOptions{JSONResponse: true, SessionTimeout: time.Minute})
	// These are already-verified identities. authenticate's own issuer, expiry,
	// revocation and membership tests remain the verification authority.
	identities := map[string]identity{
		"a":           {PlatformAdmin: true, Subject: "user-a", OrganizationRef: "org-a", Tenant: "tenant-a", RuntimeTenantRef: "tenant-a"},
		"a-refreshed": {PlatformAdmin: true, Token: "fresh-token", Subject: "user-a", OrganizationRef: "org-a", Tenant: "tenant-a", RuntimeTenantRef: "tenant-a"},
		"a-reduced":   {Role: "viewer", Subject: "user-a", OrganizationRef: "org-a", Tenant: "tenant-a", RuntimeTenantRef: "tenant-a"},
		"b":           {Subject: "user-b", OrganizationRef: "org-a", Tenant: "tenant-a", RuntimeTenantRef: "tenant-a"},
		"other-org":   {Subject: "user-a", OrganizationRef: "org-b", Tenant: "tenant-a", RuntimeTenantRef: "tenant-a"},
	}
	endpoint := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id, ok := identities[strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")]
		if !ok {
			w.WriteHeader(401)
			return
		}
		withVerifiedMCPSession(transport).ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
	}))
	defer endpoint.Close()
	request := func(method, token, sessionID, body string) *http.Response {
		t.Helper()
		r, _ := http.NewRequest(method, endpoint.URL, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+token)
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Accept", "application/json, text/event-stream")
		r.Header.Set("Mcp-Protocol-Version", "2025-03-26")
		if sessionID != "" {
			r.Header.Set("Mcp-Session-Id", sessionID)
		}
		resp, err := http.DefaultClient.Do(r)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}
	resp := request("POST", "a", "", `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"fixture","version":"1"},"capabilities":{}}}`)
	sessionID := resp.Header.Get("Mcp-Session-Id")
	resp.Body.Close()
	if resp.StatusCode != 200 || sessionID == "" {
		t.Fatalf("init status=%d session=%q", resp.StatusCode, sessionID)
	}
	toolCall := `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_fixture","arguments":{}}}`
	for _, token := range []string{"b", "other-org"} {
		for _, method := range []string{"POST", "GET", "DELETE"} {
			resp = request(method, token, sessionID, toolCall)
			resp.Body.Close()
			if resp.StatusCode != 403 {
				t.Fatalf("%s %s session status=%d want403", token, method, resp.StatusCode)
			}
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("other principal reached handler %d times", calls.Load())
	}
	resp = request("POST", "a-refreshed", sessionID, toolCall)
	var result struct{ Error any }
	_ = json.NewDecoder(resp.Body).Decode(&result)
	resp.Body.Close()
	if resp.StatusCode != 200 || result.Error != nil || calls.Load() != 1 {
		t.Fatalf("refreshed caller status=%d error=%v calls=%d", resp.StatusCode, result.Error, calls.Load())
	}
	if last.Load().(identity).Token != "fresh-token" {
		t.Fatalf("handler used initial identity token instead of refreshed token")
	}
	resp = request("POST", "a-reduced", sessionID, toolCall)
	resp.Body.Close()
	if resp.StatusCode != 200 || last.Load().(identity).PlatformAdmin || last.Load().(identity).Role != "viewer" {
		t.Fatal("handler retained initialization-time permissions")
	}
	resp = request("DELETE", "a-refreshed", sessionID, "")
	resp.Body.Close()
	if resp.StatusCode != 204 {
		t.Fatalf("delete status=%d", resp.StatusCode)
	}
	active := 0
	for range ms.Sessions() {
		active++
	}
	if active != 0 {
		t.Fatalf("closed session count=%d", active)
	}
}
