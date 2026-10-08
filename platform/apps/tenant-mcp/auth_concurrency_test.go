package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestAuthenticateCoalescesConcurrentSessionLookupsWithoutCaching(t *testing.T) {
	s := &server{}
	var userCalls, resolveCalls atomic.Int32
	role := "operator"
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		userCalls.Add(1)
		time.Sleep(75 * time.Millisecond)
		writeJSON(w, http.StatusOK, map[string]string{"id": "user-1"})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resolveCalls.Add(1)
		if r.URL.Path != "/v1/resolve-supabase" {
			t.Errorf("unexpected control path %s", r.URL.Path)
		}
		writeJSON(w, http.StatusOK, map[string]string{"tenantRef": "re8ch", "role": role, "defaultNamespace": "re8ch-default"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")

	const callers = 16
	start := make(chan struct{})
	var wg sync.WaitGroup
	for range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			id, err := s.authenticate(context.Background(), fixtureToken(), "re8ch")
			if err != nil || id.Tenant != "re8ch" || id.Role != "operator" {
				t.Errorf("concurrent authentication: id=%+v err=%v", id, err)
			}
		}()
	}
	close(start)
	wg.Wait()
	if userCalls.Load() != 1 || resolveCalls.Load() != 1 {
		t.Fatalf("same-session concurrent requests performed %d GoTrue and %d resolve calls, want one each", userCalls.Load(), resolveCalls.Load())
	}

	// A later request must not use an old authorization decision. In particular,
	// a role or active-organization change must be visible after the first flight.
	role = "viewer"
	id, err := s.authenticate(context.Background(), fixtureToken(), "re8ch")
	if err != nil || id.Role != "viewer" || userCalls.Load() != 2 || resolveCalls.Load() != 2 {
		t.Fatalf("later request reused stale decision: id=%+v err=%v GoTrue=%d resolve=%d", id, err, userCalls.Load(), resolveCalls.Load())
	}
}

func TestAuthenticateDoesNotCoalesceDifferentTokens(t *testing.T) {
	s := &server{}
	var calls atomic.Int32
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		claims, err := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"tenantRef": "re8ch", "role": "viewer", "defaultNamespace": "re8ch-default"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	var wg sync.WaitGroup
	for _, subject := range []string{"user-1", "user-2"} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := s.authenticate(context.Background(), fixtureTokenFor(subject), "re8ch"); err != nil {
				t.Errorf("authenticate %s: %v", subject, err)
			}
		}()
	}
	wg.Wait()
	if calls.Load() != 2 {
		t.Fatalf("different tokens shared an authentication flight: %d calls", calls.Load())
	}
}
