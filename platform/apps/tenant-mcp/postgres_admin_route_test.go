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

func TestPostgresSQLUsesAdminModuleAndRetainsCallerAndDatabaseGuards(t *testing.T) {
	var calls, nativeRequests atomic.Int32
	admin := mcp.NewServer(&mcp.Implementation{Name: "admin-profile", Version: "1"}, nil)
	mcp.AddTool(admin, &mcp.Tool{Name: "postgres_admin_sql"},
		func(_ context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
			calls.Add(1)
			return nil, map[string]any{"profile": "admin", "arguments": input, "auditRef": "audit://fixture/sql"}, nil
		})
	upstream := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return admin }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}))
	defer upstream.Close()
	native := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		nativeRequests.Add(1)
		http.Error(w, "wrong tool catalog", http.StatusBadRequest)
	}))
	defer native.Close()
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{
		"admin":  {URL: upstream.URL, AuthMode: "none"},
		"native": {URL: native.URL, AuthMode: "none"},
	})
	t.Setenv("PLATFORM_MODULE_REGISTRY", path)
	for _, tc := range []struct {
		name          string
		platformAdmin bool
		database      string
		allowed       bool
	}{
		{"tenant admin is insufficient", false, "re8ch_identity", false},
		{"database remains bounded", true, "postgres", false},
		{"system read", true, "re8ch_identity", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			s := &server{}
			defer func() {
				if s.modules != nil {
					s.modules.close()
				}
			}()
			ms := mcp.NewServer(&mcp.Implementation{Name: "platform-fixture", Version: "1"}, nil)
			s.registerPlatformTools(ms)
			handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return ms }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
			endpoint := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				id := identity{Subject: "admin-user", Tenant: "admin", PlatformAdmin: tc.platformAdmin}
				handler.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
			}))
			defer endpoint.Close()
			client := mcp.NewClient(&mcp.Implementation{Name: "routing-test", Version: "1"}, nil)
			session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint.URL, DisableStandaloneSSE: true}, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer session.Close()
			result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "platform_postgres_admin_sql", Arguments: map[string]any{
				"database": tc.database, "sql": "SELECT 1", "readOnly": true, "maxRows": 1,
			}})
			if !tc.allowed {
				if err == nil && !result.IsError {
					t.Fatal("forbidden SQL call accepted")
				}
				if calls.Load() != 0 {
					t.Fatal("rejected caller/database reached SQL backend")
				}
				return
			}
			if err != nil || result.IsError {
				t.Fatalf("SQL read failed: %v %#v", err, result)
			}
			content, ok := result.StructuredContent.(map[string]any)
			if !ok || content["profile"] != "admin" || content["auditRef"] != "audit://fixture/sql" {
				t.Fatalf("admin result lost: %#v", result)
			}
			args := content["arguments"].(map[string]any)
			if args["database"] != tc.database || args["sql"] != "SELECT 1" || args["readOnly"] != true || args["maxRows"] != float64(1) {
				t.Fatalf("SQL arguments changed: %#v", args)
			}
		})
	}
	if calls.Load() != 1 || nativeRequests.Load() != 0 {
		t.Fatalf("wrong SQL route: admin=%d native requests=%d", calls.Load(), nativeRequests.Load())
	}
}
