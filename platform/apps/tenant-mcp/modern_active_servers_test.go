package main

import (
	"context"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func TestModernServerSharingEndsWithActiveRequests(t *testing.T) {
	registry := &activeMCPServers{}
	var made atomic.Int32
	seen := make(chan *mcp.Server, 4)
	factory := func(*http.Request) *mcp.Server {
		made.Add(1)
		return mcp.NewServer(&mcp.Implementation{Name: "scope-fixture", Version: "1"}, nil)
	}
	handler := registry.handler(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		seen <- r.Context().Value(activeMCPServerKey{}).(*mcp.Server)
		if r.URL.Query().Get("listen") == "true" {
			<-r.Context().Done()
		}
	}), factory)
	id := identity{Subject: "a", Tenant: "tenant-a", OrganizationRef: "org-a", Token: "old-sensitive-token"}
	ctx, cancel := context.WithCancel(context.WithValue(context.Background(), identityKey{}, id))
	done := make(chan struct{})
	go func() {
		defer close(done)
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/mcp?listen=true", nil).WithContext(ctx))
	}()
	first := <-seen
	id.Token = "refreshed-sensitive-token"
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/mcp", nil).WithContext(context.WithValue(context.Background(), identityKey{}, id)))
	if <-seen != first || made.Load() != 1 {
		t.Fatal("refresh did not share active subscription server")
	}
	id.Subject = "other"
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/mcp", nil).WithContext(context.WithValue(context.Background(), identityKey{}, id)))
	if <-seen == first {
		t.Fatal("different principal shared registry")
	}
	id.Subject = "a"
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/self-service/mcp", nil).WithContext(context.WithValue(context.Background(), identityKey{}, id)))
	if <-seen == first {
		t.Fatal("different public profile shared registry")
	}
	cancel()
	<-done
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if len(registry.entries) != 0 {
		t.Fatal("completed requests retained user registry")
	}
}
