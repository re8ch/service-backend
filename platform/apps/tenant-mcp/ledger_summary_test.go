package main

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

func TestUsageSummaryAggregatesWithoutDoubleCountingOutbox(t *testing.T) {
	db, err := openLedger(filepath.Join(t.TempDir(), "usage.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	s := &server{ledger: db}
	insert := func(table, id, tenant, resource, meter string, quantity float64) {
		t.Helper()
		data, err := json.Marshal(map[string]any{"eventId": id, "meter": meter, "unit": "second", "quantity": quantity})
		if err != nil {
			t.Fatal(err)
		}
		if table == "received_usage" {
			_, err = db.Exec(`INSERT INTO received_usage VALUES(?,?,?,?,?)`, id, tenant, resource, string(data), "2026-10-09T00:00:00Z")
		} else {
			_, err = db.Exec(`INSERT INTO usage_outbox VALUES(?,?,?,?,?,?)`, id, tenant, resource, string(data), nil, "2026-10-09T00:00:00Z")
		}
		if err != nil {
			t.Fatal(err)
		}
	}
	insert("usage_outbox", "event-1", "re8ch", "service-a", "cpu", 2)
	insert("usage_outbox", "event-2", "re8ch", "service-a", "cpu", 3)
	insert("received_usage", "event-1", "re8ch", "service-a", "cpu", 5)
	insert("received_usage", "event-3", "re8ch", "service-b", "memory", 7)
	insert("received_usage", "event-4", "other", "service-a", "cpu", 100)

	all, err := s.usageSummary("re8ch", "")
	if err != nil {
		t.Fatal(err)
	}
	if all["eventCount"] != 3 || all["resourceRef"] != nil {
		t.Fatalf("unexpected tenant summary: %+v", all)
	}
	totals := all["totals"].(map[string]float64)
	if totals["cpu:second"] != 8 || totals["memory:second"] != 7 || len(totals) != 2 {
		t.Fatalf("incorrect deduplicated totals: %+v", totals)
	}

	resource, err := s.usageSummary("re8ch", "service-a")
	if err != nil {
		t.Fatal(err)
	}
	if resource["eventCount"] != 2 || resource["resourceRef"] != "service-a" || resource["totals"].(map[string]float64)["cpu:second"] != 8 {
		t.Fatalf("unexpected resource summary: %+v", resource)
	}

	empty, err := s.usageSummary("re8ch", "missing")
	if err != nil {
		t.Fatal(err)
	}
	if empty["eventCount"] != 0 || len(empty["totals"].(map[string]float64)) != 0 {
		t.Fatalf("unexpected empty summary: %+v", empty)
	}
}
