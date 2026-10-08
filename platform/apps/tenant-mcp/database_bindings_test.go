package main

import (
	"strings"
	"testing"
)

func TestDatabaseBindingMetadataNeverReturnsValues(t *testing.T) {
	const output = "pg-ledger-credentials\tuid-123\tledger\tPGDATABASE,DATABASE_URL_RW,DATABASE_URL_RO,\n"
	value, err := parseDatabaseBindings(output, "re8ch", "re8ch-default")
	if err != nil {
		t.Fatal(err)
	}
	items := value["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("unexpected database bindings: %v", items)
	}
	item := items[0].(map[string]any)
	if item["secretRef"] != "re8ch-default/pg-ledger-credentials" || item["databaseState"] != "unverified" {
		t.Fatalf("binding was misrepresented: %v", item)
	}
	if strings.Contains(databaseBindingTemplate, "{{$value}}") || strings.Contains(databaseBindingTemplate, "{{.data}}") {
		t.Fatal("database binding template can emit Secret values")
	}
	for _, bad := range []string{"pg-ledger\tuid\t<no value>\tDATABASE_URL_RW,\n", "pg-ledger\tuid\tledger\tbad/key,\n"} {
		if _, err := parseDatabaseBindings(bad, "re8ch", "re8ch-default"); err == nil {
			t.Fatalf("invalid binding metadata accepted: %q", bad)
		}
	}
}
