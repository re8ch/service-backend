package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestObservabilityStatusUsesReadProfileAndRetainsAdminGate(t *testing.T) {
	var reads, operates atomic.Int32
	observe := mcp.NewServer(&mcp.Implementation{Name: "observe-profile", Version: "1"}, nil)
	mcp.AddTool(observe, &mcp.Tool{Name: "get_observability_platform_status"},
		func(context.Context, *mcp.CallToolRequest, struct{}) (*mcp.CallToolResult, any, error) {
			reads.Add(1)
			return nil, map[string]any{"profile": "observe", "healthy": true}, nil
		})
	cloud := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return observe }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}))
	defer cloud.Close()
	infra := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		operates.Add(1)
		http.Error(w, "Forbidden", http.StatusForbidden)
	}))
	defer infra.Close()
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{
		"cloud": {URL: cloud.URL, AuthMode: "none"},
		"infra": {URL: infra.URL, AuthMode: "none"},
	})
	t.Setenv("PLATFORM_MODULE_REGISTRY", path)
	for _, admin := range []bool{false, true} {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		s := &server{}
		ms := mcp.NewServer(&mcp.Implementation{Name: "platform-fixture", Version: "1"}, nil)
		s.registerPlatformTools(ms)
		handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return ms }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
		endpoint := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			id := identity{Subject: "admin-user", Tenant: "admin", PlatformAdmin: admin}
			handler.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
		}))
		client := mcp.NewClient(&mcp.Implementation{Name: "routing-test", Version: "1"}, nil)
		session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint.URL, DisableStandaloneSSE: true}, nil)
		if err != nil {
			t.Fatal(err)
		}
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "platform_observability_status", Arguments: map[string]any{}})
		if admin {
			if err != nil || result.IsError {
				t.Fatalf("authorized read failed: %v %#v", err, result)
			}
			content, ok := result.StructuredContent.(map[string]any)
			if !ok || content["profile"] != "observe" {
				t.Fatalf("observe result lost: %#v", result)
			}
		} else {
			if err == nil && !result.IsError {
				t.Fatal("non-platform-admin read was accepted")
			}
			if reads.Load() != 0 {
				t.Fatal("unauthorized caller reached observe backend")
			}
		}
		session.Close()
		endpoint.Close()
		if s.modules != nil {
			s.modules.close()
		}
		cancel()
	}
	if reads.Load() != 1 || operates.Load() != 0 {
		t.Fatalf("wrong backend profile: observe calls=%d, operate HTTP requests=%d", reads.Load(), operates.Load())
	}
}
