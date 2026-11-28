package main

import "testing"

func TestBindRuntimeSQLPostgreSQL(t *testing.T) {
	query := "SELECT * FROM tenant_runtime.instances WHERE tenant_ref=? AND state=?"
	want := "SELECT * FROM tenant_runtime.instances WHERE tenant_ref=$1 AND state=$2"
	if got := bindRuntimeSQL(query, "postgres"); got != want {
		t.Fatalf("bindRuntimeSQL() = %q, want %q", got, want)
	}
}

func TestBindRuntimeSQLLeavesSQLiteUnchanged(t *testing.T) {
	query := "SELECT * FROM instances WHERE tenant_ref=?"
	if got := bindRuntimeSQL(query, "sqlite"); got != query {
		t.Fatalf("bindRuntimeSQL() = %q, want unchanged query", got)
	}
}
