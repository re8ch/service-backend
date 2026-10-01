package main

import (
	"path/filepath"
	"testing"
)

func TestBYOCRegistrationTenantIsolation(t *testing.T) {
	s := testServer(t)
	var err error
	s.ledger, err = openLedger(filepath.Join(t.TempDir(), "services.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.ledger.Close() })
	registered, err := s.callBYOC(identity{Tenant: "acme"}, "byoc_cluster_register", map[string]any{"cluster_uid": "cluster-one", "region_ref": "loudi", "issuer": "https://cluster.example.com", "public_key": "test-key"})
	if err != nil {
		t.Fatal(err)
	}
	if registered.(map[string]any)["tenantRef"] != "acme" {
		t.Fatal("wrong registration tenant")
	}
	other, err := s.callBYOC(identity{Tenant: "other"}, "byoc_registration_list", map[string]any{"tenant_ref": "acme"})
	if err != nil || len(other.(map[string]any)["registrations"].([]map[string]any)) != 0 {
		t.Fatalf("cross-tenant registration visible: %v %v", other, err)
	}
	list, err := s.callBYOC(identity{Tenant: "acme"}, "byoc_registration_list", nil)
	if err != nil || len(list.(map[string]any)["registrations"].([]map[string]any)) != 1 {
		t.Fatalf("own registration hidden: %v %v", list, err)
	}
}
