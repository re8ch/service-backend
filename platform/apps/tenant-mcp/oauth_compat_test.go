package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOAuthTokenCompatNormalizesLegacyErrors(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Basic fixture" {
			t.Fatal("authorization header was not forwarded")
		}
		writeJSON(w, http.StatusBadRequest, map[string]any{"code": 400, "error_code": "invalid_credentials", "msg": "refresh token is invalid"})
	}))
	defer upstream.Close()
	t.Setenv("OAUTH_TOKEN_UPSTREAM_URL", upstream.URL)
	req := httptest.NewRequest(http.MethodPost, "/oauth/token/compat", strings.NewReader("grant_type=refresh_token&refresh_token=secret"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Authorization", "Basic fixture")
	response := httptest.NewRecorder()
	oauthTokenCompat(response, req)
	var body map[string]any
	if response.Code != 400 || json.Unmarshal(response.Body.Bytes(), &body) != nil {
		t.Fatalf("unexpected response: %d %s", response.Code, response.Body.String())
	}
	if body["error"] != "invalid_grant" || body["error_description"] != "refresh token is invalid" || body["error_code"] != "invalid_credentials" {
		t.Fatalf("error was not forward-compatible: %#v", body)
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("token response may be cached")
	}
}

func TestOAuthTokenCompatPreservesSuccessBody(t *testing.T) {
	expected := `{"access_token":"opaque","token_type":"bearer","expires_in":3600}`
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200); _, _ = w.Write([]byte(expected)) }))
	defer upstream.Close()
	t.Setenv("OAUTH_TOKEN_UPSTREAM_URL", upstream.URL)
	response := httptest.NewRecorder()
	oauthTokenCompat(response, httptest.NewRequest(http.MethodPost, "/oauth/token/compat", strings.NewReader("grant_type=authorization_code")))
	if response.Code != 200 || response.Body.String() != expected {
		t.Fatalf("success changed: %d %q", response.Code, response.Body.String())
	}
}
