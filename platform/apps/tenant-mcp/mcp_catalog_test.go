package main

import (
	"context"
	"net/http/httptest"
	"path/filepath"
	"strings"
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
			ServiceName: "orders", Class: "integration.mcp.byoc.v1", Endpoint: endpoint, AudienceOrgRefs: []string{"acme"}, Meters: map[string]string{"requests": "request"},
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
	d := mcpCatalogDefinition{ServiceName: "custom", Class: "integration.mcp.managed.v1", Endpoint: "https://custom.example/mcp", AuthMode: "service-token", AudienceOrgRefs: []string{"acme"}, Meters: map[string]string{"requests": "request"}, Tools: []mcpCatalogTool{{Name: "postgresql_drop"}}}
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

func TestMCPCatalogOutboxIsOrderedIdempotentAndRollbackable(t *testing.T) {
	s := catalogFixture(t)
	definition := func(tool string) *mcpCatalogDefinition {
		return &mcpCatalogDefinition{ServiceName: "orders", Class: "integration.mcp.byoc.v1", Endpoint: "https://orders.example/mcp", AuthMode: "binding-reference", AudienceOrgRefs: []string{"acme"}, Meters: map[string]string{"requests": "request"}, Tools: []mcpCatalogTool{{Name: tool}}}
	}
	first, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "apply", TenantRef: "acme", OperationKey: "register-1", Definition: definition("orders_search")}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	replayed, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "apply", TenantRef: "acme", OperationKey: "register-1", Definition: definition("orders_search")}, "operator")
	if err != nil || replayed["idempotentReplay"] != true {
		t.Fatalf("idempotent replay failed: %#v %v", replayed, err)
	}
	second, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "apply", TenantRef: "acme", OperationKey: "change-2", Definition: definition("orders_get")}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	withdrawn, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "withdraw", TenantRef: "acme", OperationKey: "withdraw-3", Revision: second["revision"].(string)}, "operator")
	if err != nil || withdrawn["state"] != "paused" {
		t.Fatalf("withdraw failed: %#v %v", withdrawn, err)
	}
	rolled, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "rollback", TenantRef: "acme", OperationKey: "rollback-4", Revision: first["revision"].(string)}, "operator")
	if err != nil || rolled["state"] != "active" {
		t.Fatalf("rollback failed: %#v %v", rolled, err)
	}
	rows, err := s.ledger.Query(`SELECT sequence,event_id,revision FROM mcp_catalog_outbox ORDER BY sequence`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var sequences []int64
	var events, revisions []string
	for rows.Next() {
		var seq int64
		var event, revision string
		if rows.Scan(&seq, &event, &revision) != nil {
			t.Fatal("scan")
		}
		sequences = append(sequences, seq)
		events = append(events, event)
		revisions = append(revisions, revision)
	}
	if len(sequences) != 4 || sequences[0] >= sequences[1] || sequences[1] >= sequences[2] || sequences[2] >= sequences[3] {
		t.Fatalf("outbox order is wrong: %v", sequences)
	}
	if events[0] == events[1] || revisions[3] == first["revision"] {
		t.Fatalf("events or rollback revision were not immutable: %v %v", events, revisions)
	}
}

func TestMCPCatalogEventsRejectsInvalidCursor(t *testing.T) {
	s := catalogFixture(t)
	req := httptest.NewRequest("GET", "/internal/mcp/catalog/events", nil)
	req.Header.Set("Authorization", "Bearer internal")
	req.Header.Set("X-RE8CH-Tenant-Ref", "acme")
	req.Header.Set("Last-Event-ID", "stale")
	t.Setenv("MCP_CATALOG_INTERNAL_TOKEN", "internal")
	response := httptest.NewRecorder()
	s.mcpCatalogEventsHTTP(response, req)
	if response.Code != 400 || !strings.Contains(response.Body.String(), "invalid Last-Event-ID") {
		t.Fatalf("invalid cursor accepted: %d %s", response.Code, response.Body.String())
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
