package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

func TestOrganizationCallsKeepCallerIdentityAndNeverRetryWrites(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "POST" || r.URL.Path != "/v2/organizations" {
			t.Errorf("wrong request: %s %s", r.Method, r.URL.Path)
		}
		switch r.Header.Get("Authorization") {
		case "Bearer alice":
			_, _ = w.Write([]byte(`{"caller":"alice"}`))
		case "Bearer bob":
			_, _ = w.Write([]byte(`{"caller":"bob"}`))
		case "Bearer failing":
			w.WriteHeader(503)
			_, _ = w.Write([]byte(`{"error":"unavailable"}`))
		default:
			t.Error("missing caller bearer")
			w.WriteHeader(401)
		}
	}))
	defer upstream.Close()
	t.Setenv("PLATFORM_ORGANIZATIONS_URL", upstream.URL+"/v2/organizations")
	input := organizationInput{Action: "create", TenantRef: "example", DisplayName: "Example"}
	var wg sync.WaitGroup
	for _, name := range []string{"alice", "bob"} {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			ctx := context.WithValue(context.Background(), identityKey{}, identity{Subject: name, Token: name, Tenant: "admin", PlatformAdmin: true})
			result, err := organizationCall(ctx, input)
			if err != nil || result.(map[string]any)["caller"] != name {
				t.Errorf("caller mixed: %v %v", result, err)
			}
		}(name)
	}
	wg.Wait()
	ctx := context.WithValue(context.Background(), identityKey{}, identity{Subject: "failing", Token: "failing", Tenant: "admin", PlatformAdmin: true})
	if _, err := organizationCall(ctx, input); err == nil {
		t.Fatal("failed write accepted")
	}
	if calls.Load() != 3 {
		t.Fatal("write retried")
	}
	ctx = context.WithValue(ctx, identityKey{}, identity{Subject: "ordinary", Token: "ordinary", Tenant: "example"})
	if _, err := organizationCall(ctx, input); err == nil {
		t.Fatal("ordinary user accepted")
	}
	if calls.Load() != 3 {
		t.Fatal("ordinary user reached authority")
	}
}

func TestOrganizationRoutesAreBounded(t *testing.T) {
	for _, input := range []organizationInput{
		{Action: "get", TenantRef: "../admin"},
		{Action: "list", Limit: 251},
		{Action: "members", TenantRef: "x", Offset: -1},
		{Action: "remove_member", TenantRef: "x", UserID: "../other"},
		{Action: "create", TenantRef: "x"},
		{Action: "arbitrary_proxy", TenantRef: "x"},
	} {
		if _, _, _, err := organizationRequest(input); err == nil {
			t.Errorf("accepted invalid route: %+v", input)
		}
	}
}
