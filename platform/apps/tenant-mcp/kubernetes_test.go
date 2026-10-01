package main

import "testing"

func TestTenantKubernetesObjectGuard(t *testing.T) {
	bad := map[string]any{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": map[string]any{"name": "app"}, "spec": map[string]any{"template": map[string]any{"spec": map[string]any{"volumes": []any{map[string]any{"name": "host", "hostPath": map[string]any{"path": "/"}}}}}}}
	if err := validateTenantObject(bad, "acme"); err == nil {
		t.Fatal("hostPath accepted")
	}
	good := map[string]any{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": map[string]any{"name": "app"}, "spec": map[string]any{"template": map[string]any{"spec": map[string]any{"containers": []any{map[string]any{"name": "app", "image": "example"}}}}}}
	if err := validateTenantObject(good, "acme"); err != nil {
		t.Fatal(err)
	}
	labeled, err := labelTenantObject(good, "acme", "acme")
	if err != nil {
		t.Fatal(err)
	}
	labels := object(object(labeled["metadata"])["labels"])
	if labels["tenancy.re8ch.com/tenant"] != "acme" || labels["birfucate.re8ch.com/metered"] != "true" {
		t.Fatalf("missing metering labels: %v", labels)
	}
}
