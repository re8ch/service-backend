package main

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"testing"
	"time"
)

func authenticatedMCPFixture(t *testing.T) (*httptest.Server, string) {
	t.Helper()
	auth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/user":
			writeJSON(w, 200, map[string]string{"id": "user-1"})
		case "/memberships":
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "admin", "tenant_role": "admin"}})
		case "/v1/resolve-supabase":
			writeJSON(w, 200, map[string]string{"tenantRef": "admin", "role": "admin", "defaultNamespace": "admin-system"})
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(auth.Close)
	t.Setenv("SUPABASE_USER_URL", auth.URL+"/user")
	t.Setenv("PLATFORM_MEMBERSHIP_URL", auth.URL+"/memberships")
	t.Setenv("PLATFORM_SESSION_URL", "")
	t.Setenv("TENANT_CONTROL_URL", auth.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "fixture-claim")
	endpoint := httptest.NewServer(testServer(t).handler())
	t.Cleanup(endpoint.Close)
	return endpoint, fixtureToken()
}
func modernHTTP(t *testing.T, endpoint, token, method string) (*http.Response, []byte) {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": "fixture", "method": method, "params": map[string]any{"_meta": map[string]any{"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientInfo": map[string]string{"name": "fixture", "version": "1"}, "io.modelcontextprotocol/clientCapabilities": map[string]any{}}}})
	req, _ := http.NewRequest("POST", endpoint, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Protocol-Version", "2026-07-28")
	req.Header.Set("Mcp-Method", method)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	return resp, raw
}
func TestActualHandlerModernDiscoveryIsStatelessConsistentAndUsable(t *testing.T) {
	endpoint, token := authenticatedMCPFixture(t)
	var previous []byte
	for range 3 {
		resp, raw := modernHTTP(t, endpoint.URL+"/mcp", token, "server/discover")
		t.Logf("actual-production-handler discover HTTP%d sessionPresent=%v body=%s", resp.StatusCode, resp.Header.Get("Mcp-Session-Id") != "", raw)
		if resp.StatusCode != 200 {
			t.Fatal(string(raw))
		}
		var envelope struct {
			Result struct {
				SupportedVersions []string `json:"supportedVersions"`
			}
			Error any
		}
		json.Unmarshal(raw, &envelope)
		if envelope.Error != nil || !slices.Contains(envelope.Result.SupportedVersions, "2026-07-28") {
			t.Fatalf("discovery omits requested modern protocol: %s", raw)
		}
		if resp.Header.Get("Mcp-Session-Id") != "" {
			t.Fatal("modern discovery created session")
		}
		if previous != nil && !bytes.Equal(previous, raw) {
			t.Fatal("same discovery changed")
		}
		previous = raw
	}
	resp, raw := modernHTTP(t, endpoint.URL+"/mcp", token, "tools/list")
	if resp.StatusCode != 200 {
		t.Fatal(string(raw))
	}
	var envelope struct {
		Result struct{ Tools []struct{ Name string } }
		Error  any
	}
	json.Unmarshal(raw, &envelope)
	found := false
	for _, tool := range envelope.Result.Tools {
		if tool.Name == "tenant_access_status" {
			found = true
		}
	}
	if !found || envelope.Error != nil {
		t.Fatal("actual fullregistry tools unavailable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	client := mcp.NewClient(&mcp.Implementation{Name: "full-handler-sdk", Version: "1"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint.URL + "/mcp", HTTPClient: &http.Client{Transport: platformAuthTransport{base: http.DefaultTransport, token: token, header: "Authorization"}}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	if session.InitializeResult().ProtocolVersion != "2026-07-28" || session.ID() != "" {
		t.Fatal("silently fell back legacy")
	}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "tenant_access_status", Arguments: map[string]any{}})
	if err != nil || result.IsError {
		t.Fatalf("actual Admin call: %v %#v", err, result)
	}
}

func TestActualHandlerModernAliasesAndLegacySessionRemainUsable(t *testing.T) {
	endpoint, token := authenticatedMCPFixture(t)
	for _, path := range []string{"/mcp", "/plugin/mcp", "/self-service/mcp"} {
		resp, raw := modernHTTP(t, endpoint.URL+path, token, "server/discover")
		if resp.StatusCode != 200 || !bytes.Contains(raw, []byte(`"2026-07-28"`)) {
			t.Fatalf("alias %s modern discovery: %d %s", path, resp.StatusCode, raw)
		}
	}
	call := func(body, version, name string) (*http.Response, []byte) {
		t.Helper()
		req, _ := http.NewRequest("POST", endpoint.URL+"/mcp", bytes.NewBufferString(body))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Mcp-Protocol-Version", version)
		if name != "" {
			req.Header.Set("Mcp-Method", "tools/call")
			req.Header.Set("Mcp-Name", name)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return resp, raw
	}
	aliasBody := `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"re8ch.tenant_access_status","arguments":{},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"fixture","version":"1"},"io.modelcontextprotocol/clientCapabilities":{}}}}`
	resp, raw := call(aliasBody, "2026-07-28", "re8ch.tenant_access_status")
	if resp.StatusCode != 200 || bytes.Contains(raw, []byte(`"error":`)) {
		t.Fatalf("modern alias header/body mismatch: %d %s", resp.StatusCode, raw)
	}
	resp, raw = call(aliasBody, "2026-07-28", "unrelated_tool")
	if resp.StatusCode != 400 {
		t.Fatalf("conflicting alias header accepted: %d %s", resp.StatusCode, raw)
	}
	resp, raw = call(`{"jsonrpc":"2.0","id":3,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"legacy-fixture","version":"1"}}}`, "2025-11-25", "")
	sid := resp.Header.Get("Mcp-Session-Id")
	if resp.StatusCode != 200 || sid == "" {
		t.Fatalf("legacy session missing: %d %s", resp.StatusCode, raw)
	}
	req, _ := http.NewRequest("DELETE", endpoint.URL+"/mcp", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Mcp-Session-Id", sid)
	req.Header.Set("Mcp-Protocol-Version", "2025-11-25")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 204 {
		t.Fatalf("legacy DELETE failed%d", resp.StatusCode)
	}
}
