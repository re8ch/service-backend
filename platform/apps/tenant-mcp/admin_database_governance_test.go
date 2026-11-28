package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestAdminDatabaseGovernanceIsOrganizationBoundInventoryFirstAndIdempotent(t *testing.T) {
	var calls atomic.Int32
	admin := mcp.NewServer(&mcp.Implementation{Name: "admin-database", Version: "1"}, nil)
	mcp.AddTool(admin, &mcp.Tool{Name: "postgres_admin_sql"},
		func(_ context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
			calls.Add(1)
			return nil, map[string]any{"status": "executed", "database": input["database"], "credentialMaterialReturned": false}, nil
		})
	upstream := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return admin }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}))
	defer upstream.Close()
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{"tenant_admin": {URL: upstream.URL, AuthMode: "none"}})
	t.Setenv("PLATFORM_MODULE_REGISTRY", path)
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

	inventory, inventoryHash, err := adminDatabaseInventory()
	if err != nil || inventory["organizationRef"] != "admin" || inventory["credentialMaterialReturned"] != false {
		t.Fatalf("unsafe inventory: %#v %v", inventory, err)
	}
	input := map[string]any{
		"database": "re8ch_billing", "sql": "select 1", "readOnly": false,
		"approvalId": "re8ch/service-backend#3414", "inventoryHash": inventoryHash,
		"idempotencyKey": "issue-3414-write-1", "planHash": "reviewed-plan", "apply": true,
	}
	adminContext := context.WithValue(context.Background(), identityKey{}, identity{Subject: "admin-user", Tenant: "admin", PlatformAdmin: true})
	first, err := s.callAdminDatabaseSQL(adminContext, input)
	if err != nil || first.IsError {
		t.Fatalf("first apply failed: %#v %v", first, err)
	}
	replay, err := s.callAdminDatabaseSQL(adminContext, input)
	if err != nil || replay.Meta["re8ch.com/idempotent-replay"] != true || calls.Load() != 1 {
		t.Fatalf("idempotent replay failed: %#v %v calls=%d", replay, err, calls.Load())
	}

	otherOrganization := context.WithValue(context.Background(), identityKey{}, identity{Subject: "re8ch-user", Tenant: "re8ch", PlatformAdmin: true})
	if _, err = s.callAdminDatabaseSQL(otherOrganization, input); err == nil || calls.Load() != 1 {
		t.Fatal("cross-organization database governance reached the backend")
	}
	withoutInventory := map[string]any{
		"database": "re8ch_billing", "sql": "select 1", "readOnly": false,
		"approvalId": "re8ch/service-backend#3414", "idempotencyKey": "issue-3414-write-2",
	}
	if _, err = s.callAdminDatabaseSQL(adminContext, withoutInventory); err == nil || calls.Load() != 1 {
		t.Fatal("write without inventory binding reached the backend")
	}
	changed := map[string]any{}
	for key, value := range input {
		changed[key] = value
	}
	changed["sql"] = "select 2"
	if _, err = s.callAdminDatabaseSQL(adminContext, changed); err == nil || calls.Load() != 1 {
		t.Fatal("idempotency key reuse with changed SQL reached the backend")
	}
}
