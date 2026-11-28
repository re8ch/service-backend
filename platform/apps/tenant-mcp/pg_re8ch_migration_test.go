package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestPgRe8chMigrationFacadeBindsPlanAdminAndIdempotency(t *testing.T) {
	var calls atomic.Int32
	upstreamServer := mcp.NewServer(&mcp.Implementation{Name: "supabase-studio", Version: "1"}, nil)
	mcp.AddTool(upstreamServer, &mcp.Tool{Name: "apply_migration"},
		func(_ context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
			calls.Add(1)
			if input["project_id"] != pgRe8chProjectID || input["name"] != "tenant_runtime_state_v1" || input["query"] != "CREATE TABLE tenant_runtime.test(id bigint);" {
				t.Fatalf("migration target changed: %#v", input)
			}
			return nil, map[string]any{"success": true}, nil
		})
	mcp.AddTool(upstreamServer, &mcp.Tool{Name: "execute_sql", InputSchema: map[string]any{
		"type": "object", "properties": map[string]any{"query": map[string]any{"type": "string"}},
	}}, func(_ context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
		if input["project_id"] != pgRe8chProjectID {
			t.Fatalf("readback target changed: %#v", input)
		}
		return nil, map[string]any{"query": input["query"]}, nil
	})
	mcp.AddTool(upstreamServer, &mcp.Tool{Name: "list_migrations"},
		func(context.Context, *mcp.CallToolRequest, map[string]any) (*mcp.CallToolResult, any, error) {
			return nil, map[string]any{"migrations": []any{}}, nil
		})
	mcp.AddTool(upstreamServer, &mcp.Tool{Name: "list_tables"},
		func(context.Context, *mcp.CallToolRequest, map[string]any) (*mcp.CallToolResult, any, error) {
			return nil, map[string]any{"tables": []any{}}, nil
		})
	upstream := httptest.NewServer(mcp.NewStreamableHTTPHandler(
		func(*http.Request) *mcp.Server { return upstreamServer },
		&mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true},
	))
	defer upstream.Close()

	registry := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, registry, map[string]platformBackend{"pg_re8ch": {URL: upstream.URL, AuthMode: "none"}})
	t.Setenv("PLATFORM_MODULE_REGISTRY", registry)
	s := &server{}
	var err error
	s.ledger, err = openLedger(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.ledger.Close()
	defer func() {
		if s.modules != nil {
			s.modules.close()
		}
	}()

	mcpHandler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return s.pgRe8chAdminMCP() },
		&mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	adminIdentity := identity{Subject: "admin-user", Tenant: "admin", OrganizationRef: "admin", PlatformAdmin: true}
	endpoint := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := context.WithValue(r.Context(), identityKey{}, adminIdentity)
		withVerifiedMCPSession(mcpHandler).ServeHTTP(w, r.WithContext(ctx))
	}))
	defer endpoint.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client := mcp.NewClient(&mcp.Implementation{Name: "migration-test", Version: "1"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{
		Endpoint: endpoint.URL,
		HTTPClient: &http.Client{Transport: platformAuthTransport{
			base: http.DefaultTransport, token: "fixture", header: "Authorization",
		}},
		DisableStandaloneSSE: true,
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()

	listed, err := session.ListTools(ctx, nil)
	if err != nil || len(listed.Tools) != 5 || listed.Tools[0].Name != "pg_re8ch_execute_sql" ||
		listed.Tools[1].Name != "pg_re8ch_list_migrations" || listed.Tools[2].Name != "pg_re8ch_list_tables" ||
		listed.Tools[3].Name != "pg_re8ch_migration_apply" || listed.Tools[4].Name != "pg_re8ch_migration_preview" {
		t.Fatalf("unexpected facade tools: %#v %v", listed, err)
	}
	readback, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_execute_sql", Arguments: map[string]any{
		"query": "SELECT * FROM tenant_runtime.tool_registrations",
	}})
	if err != nil || readback.IsError {
		t.Fatalf("readback failed: %#v %v", readback, err)
	}
	deniedWrite, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_execute_sql", Arguments: map[string]any{
		"query": "DELETE FROM tenant_runtime.tool_registrations",
	}})
	if err == nil && !deniedWrite.IsError {
		t.Fatal("mutating execute_sql reached the readback surface")
	}
	wrongProject, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_execute_sql", Arguments: map[string]any{
		"project_id": "pg-identity", "query": "SELECT 1",
	}})
	if err == nil && !wrongProject.IsError {
		t.Fatal("readback accepted a different Supabase project")
	}
	query := "CREATE TABLE tenant_runtime.test(id bigint);"
	preview, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_migration_preview", Arguments: map[string]any{
		"name": "tenant_runtime_state_v1", "query": query,
	}})
	if err != nil || preview.IsError {
		t.Fatalf("preview failed: %#v %v", preview, err)
	}
	plan := preview.StructuredContent.(map[string]any)
	if plan["projectId"] != pgRe8chProjectID || plan["decision"] != "preview" || plan["sqlSha256"] == "" || plan["planHash"] == "" {
		t.Fatalf("unsafe preview: %#v", plan)
	}
	apply := map[string]any{
		"name": "tenant_runtime_state_v1", "query": query,
		"planHash": plan["planHash"], "approvalId": "re8ch/service-backend#3469",
		"idempotencyKey": "issue-3469-tenant-runtime-v1",
	}
	for range 2 {
		result, callErr := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_migration_apply", Arguments: apply})
		if callErr != nil || result.IsError {
			t.Fatalf("apply/replay failed: %#v %v", result, callErr)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("migration was not idempotent: calls=%d", calls.Load())
	}

	changedPlan, _, err := pgRe8chMigrationPlan("tenant_runtime_state_v1", query+" SELECT 1;")
	if err != nil || changedPlan["sqlSha256"] == plan["sqlSha256"] {
		t.Fatal("changed SQL did not change the plan")
	}
	changedQuery := query + " SELECT 1;"
	_, changedHash, err := pgRe8chMigrationPlan("tenant_runtime_state_v1", changedQuery)
	if err != nil {
		t.Fatal(err)
	}
	changedApply := map[string]any{
		"name": "tenant_runtime_state_v1", "query": changedQuery,
		"planHash": changedHash, "approvalId": "re8ch/service-backend#3469",
		"idempotencyKey": "issue-3469-tenant-runtime-v1",
	}
	conflict, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "pg_re8ch_migration_apply", Arguments: changedApply})
	if err == nil && !conflict.IsError {
		t.Fatal("idempotencyKey reuse with changed SQL was accepted")
	}
	if calls.Load() != 1 {
		t.Fatal("rejected idempotency conflict reached Supabase")
	}
}

func TestPgRe8chMigrationPlanRejectsUnboundedInput(t *testing.T) {
	for _, input := range []pgRe8chMigrationPreviewInput{
		{Name: "Bad-Name", Query: "SELECT 1"},
		{Name: "ok_name", Query: ""},
	} {
		if _, _, err := pgRe8chMigrationPlan(input.Name, input.Query); err == nil {
			t.Fatalf("unsafe input accepted: %#v", input)
		}
	}
}

func TestPgRe8chPublicResourceMetadataAndAnonymousDenial(t *testing.T) {
	t.Setenv("PG_RE8CH_PUBLIC_MCP_URL", "https://pg-re8ch-mcp.service.re8ch.com/mcp")
	t.Setenv("PG_RE8CH_RESOURCE_METADATA_URL", "https://pg-re8ch-mcp.service.re8ch.com/.well-known/oauth-protected-resource/mcp")
	s := &server{}
	handler := s.handler()

	metadata := httptest.NewRecorder()
	handler.ServeHTTP(metadata, httptest.NewRequest(http.MethodGet, "/.well-known/oauth-protected-resource/pg-re8ch/mcp", nil))
	var document map[string]any
	if metadata.Code != http.StatusOK || json.Unmarshal(metadata.Body.Bytes(), &document) != nil || document["resource"] != "https://pg-re8ch-mcp.service.re8ch.com/mcp" {
		t.Fatalf("invalid resource metadata: status=%d body=%s", metadata.Code, metadata.Body.String())
	}

	denied := httptest.NewRecorder()
	handler.ServeHTTP(denied, httptest.NewRequest(http.MethodPost, "/pg-re8ch/mcp", nil))
	if denied.Code != http.StatusUnauthorized || denied.Header().Get("WWW-Authenticate") != `Bearer realm="https://db-rw.re8ch.com/auth/v1", resource_metadata="https://pg-re8ch-mcp.service.re8ch.com/.well-known/oauth-protected-resource/mcp"` {
		t.Fatalf("anonymous MCP denial lost metadata: status=%d header=%q", denied.Code, denied.Header().Get("WWW-Authenticate"))
	}
}
