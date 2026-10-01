package main

import (
	"path/filepath"
	"testing"
)

func TestServiceStateParityAndIdempotency(t *testing.T) {
	s := testServer(t)
	var err error
	s.ledger, err = openLedger(filepath.Join(t.TempDir(), "tenant-services.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.ledger.Close() })
	spec := map[string]any{"serviceClass": "storage.volume.v1", "planRef": "rbd", "regionRef": "loudi", "parameters": map[string]any{"sizeGiB": float64(10)}, "labels": map[string]any{"note": "中文"}}
	preview, err := s.instancePlan("acme", "volume-one", spec)
	if err != nil {
		t.Fatal(err)
	}
	if preview["requestHash"] != "sha256:6980fc71973063101862151862653b966a6a88cbdb5b6d65fd20fca55f239573" {
		t.Fatalf("canonical hash diverged from Python: %v", preview["requestHash"])
	}
	first, err := s.instanceMutate("acme", "volume-one", spec, "stable-key-1", "create")
	if err != nil {
		t.Fatal(err)
	}
	if first["state"] != "Provisioning" {
		t.Fatal("wrong provision state")
	}
	replay, err := s.instanceMutate("acme", "volume-one", spec, "stable-key-1", "create")
	if err != nil || replay["idempotentReplay"] != true {
		t.Fatalf("idempotent replay failed: %v %v", replay, err)
	}
	other := map[string]any{"serviceClass": "storage.volume.v1", "planRef": "rbd", "regionRef": "loudi", "parameters": map[string]any{"sizeGiB": float64(20)}}
	if _, err = s.instanceMutate("acme", "volume-one", other, "stable-key-1", "update"); err == nil {
		t.Fatal("idempotency key reused for a different request")
	}
	if _, err = s.instanceGet("other", "service://acme/volume-one"); err == nil {
		t.Fatal("cross-tenant service read succeeded")
	}
	if _, err = s.instanceMutate("acme", "volume-one", map[string]any{"serviceClass": "storage.volume.v1", "planRef": "rbd", "regionRef": "loudi", "parameters": map[string]any{"sizeGiB": float64(1)}}, "stable-key-2", "update"); err == nil {
		t.Fatal("volume shrink accepted")
	}
}
