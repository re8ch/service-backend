package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

func TestTenantKubernetesDeleteOnlyAllowsMutableKinds(t *testing.T) {
	for _, kind := range []string{"deployment", "statefulset", "daemonset", "job", "cronjob", "configmap", "service"} {
		if !tenantMutableKind(kind) {
			t.Fatalf("mutable kind %s rejected", kind)
		}
	}
	for _, kind := range []string{"pod", "event", "endpoint", "replicaset", "secret", "httproute"} {
		if tenantMutableKind(kind) {
			t.Fatalf("read-only or forbidden kind %s accepted for deletion", kind)
		}
	}
}

func TestReadyNamespaceRequiresMatchingTenantRef(t *testing.T) {
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/namespaces" {
			t.Fatalf("unexpected control path: %s", r.URL.Path)
		}
		writeJSON(w, 200, map[string]any{"items": []any{map[string]any{"spec": map[string]any{"tenantRef": "other", "namespaceName": "acme-default", "operation": "EnsurePresent"}, "status": map[string]any{"phase": "Ready"}}}})
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	if err := readyNamespace(context.Background(), "acme", "acme-default"); err == nil || !strings.Contains(err.Error(), "owned by the authenticated tenant") {
		t.Fatalf("foreign namespace was accepted: %v", err)
	}
}

func TestTenantKubernetesDeleteRejectsReadOnlyKindBeforeKubectl(t *testing.T) {
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"items": []any{map[string]any{"spec": map[string]any{"tenantRef": "acme", "namespaceName": "acme-default", "operation": "EnsurePresent"}, "status": map[string]any{"phase": "Ready"}}}})
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	_, err := (&server{}).callKubernetes(context.Background(), identity{Tenant: "acme", Role: "operator", DefaultNamespace: "acme-default"}, "tenant_kubernetes_delete", map[string]any{"kind": "pod", "name": "web", "apply": true})
	if err == nil || !strings.Contains(err.Error(), "workload allowlist") {
		t.Fatalf("read-only Pod delete was not rejected: %v", err)
	}
}

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

func TestTenantServiceAndWorkloadCannotBypassPublicEdge(t *testing.T) {
	for _, spec := range []map[string]any{
		{"type": "ClusterIP", "externalIPs": []any{"192.0.2.112"}},
		{"type": "NodePort"},
		{"type": "ExternalName"},
	} {
		if err := validateTenantObject(map[string]any{"kind": "Service", "spec": spec}, "acme"); err == nil {
			t.Fatalf("public Service bypass accepted: %v", spec)
		}
	}
	if err := validateTenantObject(map[string]any{"kind": "Service", "spec": map[string]any{"type": "ClusterIP", "ports": []any{map[string]any{"port": 80}}}}, "acme"); err != nil {
		t.Fatalf("ClusterIP Service rejected: %v", err)
	}
	for _, kind := range []string{"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"} {
		container := map[string]any{"name": "web", "image": "example", "ports": []any{map[string]any{"containerPort": 8080, "hostPort": 8080}}}
		podSpec := map[string]any{"containers": []any{container}}
		spec := map[string]any{"template": map[string]any{"spec": podSpec}}
		if kind == "CronJob" {
			spec = map[string]any{"jobTemplate": map[string]any{"spec": spec}}
		}
		if err := validateTenantObject(map[string]any{"kind": kind, "spec": spec}, "acme"); err == nil {
			t.Fatalf("%s hostPort accepted", kind)
		}
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
