package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

func TestConcurrentServiceListsRejectPreviousOrganizationAfterSelection(t *testing.T) {
	personal := "personal-11111111-1111-4111-8111-111111111111"
	authority := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rpc/my_tenant_runtime_contexts" || r.Header.Get("Authorization") != "Bearer session-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		writeJSON(w, http.StatusOK, []organizationContext{
			{TenantRef: personal, Active: true},
			{TenantRef: "admin", Active: false},
		})
	}))
	defer authority.Close()
	t.Setenv("IDENTITY_REST_URL", authority.URL)

	personalID := identity{Subject: "user-1", Token: "session-token", Tenant: personal, OrganizationRef: personal}
	staleAdminID := identity{Subject: "user-1", Token: "session-token", Tenant: "admin", OrganizationRef: "admin"}
	var group sync.WaitGroup
	errors := make(chan error, 64)
	for index := 0; index < 64; index++ {
		group.Add(1)
		go func(stale bool) {
			defer group.Done()
			id := personalID
			if stale {
				id = staleAdminID
			}
			errors <- requireActiveOrganization(context.Background(), id)
		}(index%2 == 0)
	}
	group.Wait()
	close(errors)
	accepted, rejected := 0, 0
	for err := range errors {
		if err == nil {
			accepted++
		} else {
			rejected++
		}
	}
	if accepted != 32 || rejected != 32 {
		t.Fatalf("active Personal=%d stale Admin rejected=%d, want 32/32", accepted, rejected)
	}
	for _, tool := range []string{"service_instance_list", "service_binding_list"} {
		if err := requireActiveOrganization(context.Background(), staleAdminID); err == nil {
			t.Fatalf("%s accepted the previous Admin organization", tool)
		}
	}
}

func TestActiveOrganizationVerificationFailsClosed(t *testing.T) {
	for name, rows := range map[string][]organizationContext{
		"none":      {{TenantRef: "admin"}},
		"ambiguous": {{TenantRef: "admin", Active: true}, {TenantRef: "re8ch", Active: true}},
		"invalid":   {{TenantRef: "../admin", Active: true}},
	} {
		t.Run(name, func(t *testing.T) {
			authority := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				writeJSON(w, http.StatusOK, rows)
			}))
			defer authority.Close()
			t.Setenv("IDENTITY_REST_URL", authority.URL)
			id := identity{Subject: "user-1", Token: "session-token", Tenant: "admin", OrganizationRef: "admin"}
			if err := requireActiveOrganization(context.Background(), id); err == nil {
				t.Fatal("invalid authority state was accepted")
			}
		})
	}
}
