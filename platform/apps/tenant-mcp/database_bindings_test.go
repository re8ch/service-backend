package main

import (
	"strings"
	"testing"
)

func TestDatabaseBindingMetadataNeverReturnsValues(t *testing.T) {
	const output = "pg-ledger-credentials\tuid-123\tledger\tcomplete\tReady\tPGDATABASE,DATABASE_URL_RW,DATABASE_URL_RO,\n"
	value, err := parseDatabaseBindings(output, "re8ch", "re8ch-default")
	if err != nil {
		t.Fatal(err)
	}
	items := value["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("unexpected database bindings: %v", items)
	}
	item := items[0].(map[string]any)
	if item["secretRef"] != "re8ch-default/pg-ledger-credentials" || item["credentialState"] != "complete" || item["databaseState"] != "Ready" {
		t.Fatalf("binding was misrepresented: %v", item)
	}
	if strings.Contains(databaseBindingTemplate, "{{$value}}") || strings.Contains(databaseBindingTemplate, "{{.data}}") {
		t.Fatal("database binding template can emit Secret values")
	}
	for _, bad := range []string{"pg-ledger\tuid\t<no value>\tcomplete\tReady\tDATABASE_URL_RW,\n", "pg-ledger\tuid\tledger\tcomplete\tReady\tbad/key,\n"} {
		if _, err := parseDatabaseBindings(bad, "re8ch", "re8ch-default"); err == nil {
			t.Fatalf("invalid binding metadata accepted: %q", bad)
		}
	}
	for _, output := range []string{
		"pg-ledger-credentials\tuid-123\tledger\tpartial\tReady\tPGDATABASE,\n",
		"pg-ledger-credentials\tuid-123\tledger\tcomplete\tBogus\tPGDATABASE,\n",
	} {
		value, err := parseDatabaseBindings(output, "re8ch", "re8ch-default")
		if err != nil {
			t.Fatal(err)
		}
		if value["items"].([]any)[0].(map[string]any)["databaseState"] != "unverified" {
			t.Fatalf("untrusted database state was accepted: %v", value)
		}
	}
}
