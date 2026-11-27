package main

import (
	"strings"
	"testing"
)

func TestNativeServiceToolPublicationIsReadOnly(t *testing.T) {
	for _, tc := range []struct {
		backend string
		name    string
		want    bool
	}{
		{"pg_re8ch", "execute_sql", true},
		{"pg_identity", "list_tables", true},
		{"pg_billing", "apply_migration", false},
		{"grafana", "search_dashboards", true},
		{"grafana", "update_dashboard", false},
	} {
		if got := nativeServiceToolAllowed(tc.backend, tc.name); got != tc.want {
			t.Fatalf("nativeServiceToolAllowed(%q, %q) = %v, want %v", tc.backend, tc.name, got, tc.want)
		}
	}
}

func TestSupabaseExecuteSQLRejectsWritesAndMultipleStatements(t *testing.T) {
	for _, query := range []string{
		"select id from users",
		"WITH active AS (SELECT id FROM users) SELECT * FROM active",
		"explain select * from invoices",
	} {
		if err := validateNativeServiceCall("pg_re8ch", "execute_sql", map[string]any{"query": query}); err != nil {
			t.Fatalf("read-only query rejected: %v", err)
		}
	}
	for _, query := range []string{
		"update users set active = false",
		"with removed as (delete from users returning id) select * from removed",
		"select 1; delete from users",
		"",
	} {
		if err := validateNativeServiceCall("pg_re8ch", "execute_sql", map[string]any{"query": query}); err == nil {
			t.Fatalf("unsafe query accepted: %q", query)
		}
	}
}

func TestNativeServiceDescriptionsNameAdminServiceInstances(t *testing.T) {
	for _, service := range nativeServiceMCPs {
		ref := "service://admin/" + strings.ReplaceAll(service.Prefix, "_", "-")
		if !strings.HasPrefix(ref, "service://admin/") {
			t.Fatalf("invalid service reference %q", ref)
		}
	}
}
