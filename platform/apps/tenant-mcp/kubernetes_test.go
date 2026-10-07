package main

import (
	"reflect"
	"testing"
)

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

func TestSecretBrokerValidationNeverReturnsValues(t *testing.T) {
	values := map[string]string{"PASSWORD": "never-return-this", "api.key": "also-secret"}
	keys, err := secretKeys(values)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(keys, []string{"PASSWORD", "api.key"}) {
		t.Fatalf("unexpected key metadata: %v", keys)
	}
	if _, err := secretKeys(map[string]string{"bad/key": "secret"}); err == nil {
		t.Fatal("invalid Secret key accepted")
	}
}

func TestManagedExposureContract(t *testing.T) {
	tenant := "personal-11111111-1111-4111-8111-111111111111"
	hostname := exposureHostname(tenant, "demo")
	if len(hostname) > 80 || hostname[len(hostname)-len(".apps.re8ch.com"):] != ".apps.re8ch.com" {
		t.Fatalf("invalid managed hostname: %s", hostname)
	}
	owned := map[string]any{
		"metadata": map[string]any{"name": "demo", "labels": map[string]any{"tenancy.re8ch.com/managed-by": "tenant-exposure-controller", "tenancy.re8ch.com/tenant": tenant}},
		"spec":     map[string]any{"hostnames": []any{hostname}, "rules": []any{map[string]any{"backendRefs": []any{map[string]any{"name": "web", "port": float64(8080)}}}}},
	}
	if item, ok := exposureItem(owned, tenant, tenant+"-default"); !ok || item["url"] != "https://"+hostname {
		t.Fatalf("owned exposure was not sanitized: %v %v", item, ok)
	}
	if _, ok := exposureItem(owned, "another-tenant", "another-default"); ok {
		t.Fatal("cross-tenant exposure accepted")
	}
	for _, path := range []string{"", "relative", "/bad\nheader"} {
		if validHTTPPath(path) {
			t.Fatalf("invalid path accepted: %q", path)
		}
	}
}
