package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestSharedSessionSurvivesReplicaChangesAndRejectsRevocation(t *testing.T) {
	active := true
	subject := "admin-user"
	session := "login-1"
	authority := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !active {
			w.WriteHeader(401)
			return
		}
		writeJSON(w, 200, map[string]any{"subject": subject, "sessionId": session, "platformAdmin": true})
	}))
	defer authority.Close()
	t.Setenv("PLATFORM_SESSION_URL", authority.URL)
	// Direct membership/GoTrue endpoints are intentionally unavailable: shared
	// mode must use the single Auth Gateway verification path.
	t.Setenv("SUPABASE_USER_URL", "http://127.0.0.1:1")
	t.Setenv("PLATFORM_MEMBERSHIP_URL", "http://127.0.0.1:1")
	claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": subject, "session_id": session, "exp": time.Now().Add(time.Hour).Unix()})
	token := "header." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
	for i := 0; i < 2; i++ {
		replica := testServer(t)
		id, err := replica.verifySupabase(context.Background(), token)
		if err != nil || !id.PlatformAdmin || id.Subject != subject {
			t.Fatalf("replica %d lost session grant: %v", i, err)
		}
	}
	replica := testServer(t)
	subject = "other-user"
	if _, err := replica.verifySupabase(context.Background(), token); err == nil {
		t.Fatal("cross-user grant accepted")
	}
	subject = "admin-user"
	session = "other-login"
	if _, err := replica.verifySupabase(context.Background(), token); err == nil {
		t.Fatal("cross-session grant accepted")
	}
	session = "login-1"
	active = false
	if _, err := replica.verifySupabase(context.Background(), token); err == nil {
		t.Fatal("revoked session accepted")
	}
}

func TestSessionAuthorityOutageDoesNotChallengeLogin(t *testing.T) {
	authority := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
	defer authority.Close()
	t.Setenv("PLATFORM_SESSION_URL", authority.URL)
	claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": "user", "session_id": "session", "exp": time.Now().Add(time.Hour).Unix()})
	token := "header." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
	s := testServer(t)
	req := httptest.NewRequest("POST", "/mcp", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, req)
	if response.Code != 503 || response.Header().Get("WWW-Authenticate") != "" {
		t.Fatalf("outage caused login challenge: %d %v", response.Code, response.Header())
	}
}
