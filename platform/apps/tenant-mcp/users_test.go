package main

import (
	"context"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

func TestUserCallsKeepCallerIdentityAndNeverRetryWrites(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "POST" || r.URL.Path != "/v2/users" {
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
	t.Setenv("PLATFORM_USERS_URL", upstream.URL+"/v2/users")
	input := userInput{Action: "create", Email: "test@example.com", DisplayName: "Example"}
	var wg sync.WaitGroup
	for _, name := range []string{"alice", "bob"} {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			ctx := context.WithValue(context.Background(), identityKey{}, identity{Subject: name, Token: name, Tenant: "admin", PlatformAdmin: true})
			result, err := userCall(ctx, input)
			if err != nil || result.(map[string]any)["caller"] != name {
				t.Errorf("caller mixed: %v %v", result, err)
			}
		}(name)
	}
	wg.Wait()
	ctx := context.WithValue(context.Background(), identityKey{}, identity{Subject: "failing", Token: "failing", Tenant: "admin", PlatformAdmin: true})
	if _, err := userCall(ctx, input); err == nil {
		t.Fatal("failed write accepted")
	}
	if calls.Load() != 3 {
		t.Fatal("write retried")
	}
	ctx = context.WithValue(ctx, identityKey{}, identity{Subject: "ordinary", Token: "ordinary", Tenant: "example"})
	if _, err := userCall(ctx, input); err == nil {
		t.Fatal("ordinary user accepted")
	}
	if calls.Load() != 3 {
		t.Fatal("ordinary user reached authority")
	}
}

func TestUserRoutes(t *testing.T) {
	id := "11111111-1111-1111-1111-111111111111"
	for _, c := range []struct {
		input        userInput
		method, path string
	}{
		{userInput{Action: "list"}, "GET", "?page=1&perPage=50"},
		{userInput{Action: "get", UserID: id}, "GET", "/" + id},
		{userInput{Action: "create", Email: "test@example.com"}, "POST", ""},
		{userInput{Action: "update", UserID: id, DisplayName: "Updated"}, "PATCH", "/" + id},
		{userInput{Action: "delete", UserID: id}, "DELETE", "/" + id},
	} {
		method, path, _, err := userRequest(c.input)
		if err != nil || method != c.method || path != c.path {
			t.Fatalf("bad route: %v %s %s", err, method, path)
		}
	}
	for _, input := range []userInput{{Action: "get", UserID: "../admin"}, {Action: "list", PerPage: 101}, {Action: "create", Email: "bad"}, {Action: "update", UserID: id}, {Action: "proxy"}} {
		if _, _, _, err := userRequest(input); err == nil {
			t.Fatalf("invalid request accepted: %+v", input)
		}
	}
}

func TestUserToolOnlyAdvertisedToHumanAdmin(t *testing.T) {
	t.Setenv("PLATFORM_USERS_URL", "https://identity.invalid/v2/users")
	for _, id := range []identity{{PlatformAdmin: true}, {PlatformAdmin: true, Service: true}, {PlatformAdmin: false}} {
		ctx := context.Background()
		s := &server{}
		left, right := mcp.NewInMemoryTransports()
		ss, err := s.skillMCP(id).Connect(ctx, left, nil)
		if err != nil {
			t.Fatal(err)
		}
		client := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "1"}, nil)
		session, err := client.Connect(ctx, right, nil)
		if err != nil {
			t.Fatal(err)
		}
		result, err := session.ListTools(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for _, tool := range result.Tools {
			if tool.Name == "platform_user" {
				found = true
			}
		}
		if found != (id.PlatformAdmin && !id.Service) {
			t.Fatalf("incorrect user tool exposure: %+v", id)
		}
		session.Close()
		ss.Close()
	}
}
