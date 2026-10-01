package main

import (
	"path/filepath"
	"testing"
	"time"
)

func TestUsageLedgerParity(t *testing.T) {
	s := testServer(t)
	var err error
	s.ledger, err = openLedger(filepath.Join(t.TempDir(), "tenant-services.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.ledger.Close() })
	now := time.Now().UTC()
	event := map[string]any{
		"eventId": "ue_0123456789abcdef0123456789abcdef", "tenantRef": "acme", "resourceRef": "service://acme/db",
		"serviceClass": "database.postgresql.shared.v1", "meter": "instance_hours", "quantity": float64(2), "unit": "hour",
		"periodStart": now.Add(-2 * time.Hour).Format(time.RFC3339Nano), "periodEnd": now.Add(-time.Hour).Format(time.RFC3339Nano),
		"regionRef": "loudi", "sourceOperationId": "op_1", "observedAt": now.Format(time.RFC3339Nano), "schemaVersion": "usage.re8ch.com/v1",
	}
	first, err := s.receiveUsage(event)
	if err != nil || first["idempotentReplay"] != false {
		t.Fatalf("first insert: %v %v", first, err)
	}
	second, err := s.receiveUsage(event)
	if err != nil || second["idempotentReplay"] != true {
		t.Fatalf("replay: %v %v", second, err)
	}
	other := make(map[string]any)
	for key, value := range event {
		other[key] = value
	}
	other["quantity"] = float64(3)
	if _, err = s.receiveUsage(other); err == nil {
		t.Fatal("conflicting event ID accepted")
	}
	result, err := s.billingUsage("acme", now.Add(-time.Hour), now.Add(time.Hour))
	if err != nil || len(result["items"].([]map[string]any)) != 1 {
		t.Fatalf("billing summary: %v %v", result, err)
	}
	result, err = s.billingUsage("other", now.Add(-time.Hour), now.Add(time.Hour))
	if err != nil || len(result["items"].([]map[string]any)) != 0 {
		t.Fatalf("cross-tenant billing: %v %v", result, err)
	}
}
