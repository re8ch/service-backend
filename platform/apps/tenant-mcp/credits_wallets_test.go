package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCreditsWalletsUseCallerToken(t *testing.T) {
	s := testServer(t)
	token := fixtureToken()
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"tenantRef": "acme", "role": "viewer", "defaultNamespace": "acme"})
	}))
	defer control.Close()
	var requested bool
	identityREST := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requested = true
		if r.URL.Path != "/rpc/credits_wallets" || r.Method != http.MethodPost || r.Header.Get("Authorization") != "Bearer "+token {
			t.Errorf("wallet RPC used unexpected path, method, or caller token")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"wallets": []map[string]any{{"ownerKind": "personal", "balance": 42}}})
	}))
	defer identityREST.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("IDENTITY_REST_URL", identityREST.URL)
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tenant_credits_wallets_list","arguments":{}}}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != http.StatusOK || !requested || !strings.Contains(response.Body.String(), `"balance":42`) {
		t.Fatalf("wallets were not returned through caller-scoped RPC: status=%d body=%s", response.Code, response.Body.String())
	}
}
