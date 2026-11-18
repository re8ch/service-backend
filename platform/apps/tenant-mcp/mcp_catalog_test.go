package main

import (
	"context"
	"path/filepath"
	"testing"
)

func catalogFixture(t *testing.T) *server {
	t.Helper()
	s, err := open(filepath.Join(t.TempDir(), "skills.db"))
	if err != nil {
		t.Fatal(err)
	}
	s.ledger, err = openLedger(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.db.Close(); s.ledger.Close() })
	return s
}

func TestMCPCatalogIsTenantIsolatedAndIdempotent(t *testing.T) {
	s := catalogFixture(t)
	definition := func(endpoint string) *mcpCatalogDefinition {
		return &mcpCatalogDefinition{
			ServiceName: "orders", Class: "integration.mcp.byoc.v1", Endpoint: endpoint,
			AuthMode: "binding-reference", Health: "ready", Tools: []mcpCatalogTool{{Name: "orders_search"}},
		}
	}
	for _, tenant := range []string{"alpha", "beta"} {
		cmd := mcpCatalogCommand{Action: "apply", TenantRef: tenant, OperationKey: "op-1", Definition: definition("https://" + tenant + ".example/mcp")}
		first, err := s.applyMCPCatalog(context.Background(), cmd, "operator")
		if err != nil {
			t.Fatal(err)
		}
		second, err := s.applyMCPCatalog(context.Background(), cmd, "operator")
		if err != nil {
			t.Fatal(err)
		}
		if first["revision"] != second["revision"] || second["idempotentReplay"] != true {
			t.Fatalf("idempotency failed: %#v %#v", first, second)
		}
	}
	alpha, _, err := s.mcpCatalog(context.Background(), "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if len(alpha) != 1 || alpha[0].Definition.Endpoint != "https://alpha.example/mcp" {
		t.Fatalf("tenant leaked or missing: %#v", alpha)
	}
}

func TestMCPCatalogRejectsStandardNameOverride(t *testing.T) {
	d := mcpCatalogDefinition{ServiceName: "custom", Class: "integration.mcp.managed.v1", Endpoint: "https://custom.example/mcp", AuthMode: "service-token", Tools: []mcpCatalogTool{{Name: "postgresql_drop"}}}
	if err := validateMCPDefinition("alpha", d); err == nil {
		t.Fatal("custom provider overrode standard prefix")
	}
}

func TestMCPCatalogPauseKeepsHistory(t *testing.T) {
	s := catalogFixture(t)
	d := &mcpCatalogDefinition{ServiceName: "postgresql", Class: "database.postgresql.shared.v1", Endpoint: "http://postgresql-provider.platform.svc/mcp", AuthMode: "service-token", Tools: []mcpCatalogTool{{Name: "postgresql_instance_list"}}}
	applied, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "apply", TenantRef: "*", OperationKey: "apply-1", Definition: d}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	paused, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "pause", TenantRef: "*", OperationKey: "pause-1", Revision: applied["revision"].(string)}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	entries, _, err := s.mcpCatalog(context.Background(), "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if paused["state"] != "paused" || len(entries) != 1 || entries[0].State != "paused" {
		t.Fatalf("pause not visible: %#v %#v", paused, entries)
	}
}

func TestOpenLedgerWithdrawsLegacyPostgresqlProviderCatalogHead(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.db")
	db, err := openLedger(path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO mcp_catalog_revisions(revision,tenant_ref,service_name,definition_json,state,operation_key,actor,created_at) VALUES('old','*','postgresql-provider','{}','active','old-op','legacy','2026-01-01T00:00:00Z')`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO mcp_catalog_heads(tenant_ref,service_name,revision) VALUES('*','postgresql-provider','old')`)
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	db, err = openLedger(path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var count int
	if err = db.QueryRow(`SELECT COUNT(*) FROM mcp_catalog_heads WHERE service_name='postgresql-provider'`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("legacy catalog head survived migration: count=%d err=%v", count, err)
	}
	if err = db.QueryRow(`SELECT COUNT(*) FROM mcp_catalog_revisions WHERE service_name='postgresql-provider'`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("legacy audit revision was not preserved: count=%d err=%v", count, err)
	}
}
