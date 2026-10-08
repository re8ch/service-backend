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

func TestReadyTenantNamespacesFiltersForeignExpiredAndDuplicateEntries(t *testing.T) {
	entry := func(tenant, namespace, operation, phase, expiry string) any {
		return map[string]any{
			"spec":   map[string]any{"tenantRef": tenant, "namespaceName": namespace, "operation": operation, "expiresAt": expiry},
			"status": map[string]any{"phase": phase},
		}
	}
	value := map[string]any{"items": []any{
		entry("acme", "acme-default", "EnsurePresent", "Ready", ""),
		entry("acme", "acme-default", "EnsurePresent", "Ready", ""),
		entry("other", "other-default", "EnsurePresent", "Ready", ""),
		entry("acme", "acme-expired", "EnsurePresent", "Ready", "2000-01-01T00:00:00Z"),
		entry("acme", "acme-pending", "EnsurePresent", "Pending", ""),
		entry("acme", "acme-deleted", "EnsureAbsent", "Ready", ""),
		entry("acme", "bad/name", "EnsurePresent", "Ready", ""),
		entry("acme", "acme-future", "EnsurePresent", "Ready", "2100-01-01T00:00:00Z"),
	}}
	got := readyTenantNamespaces(value, "acme")
	want := []string{"acme-default", "acme-future"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ready namespaces = %v, want %v", got, want)
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

func TestTenantWorkloadVolumesRequireTenantLocalSources(t *testing.T) {
	for _, source := range []map[string]any{
		{"emptyDir": map[string]any{}},
		{"configMap": map[string]any{"name": "app-config"}},
		{"secret": map[string]any{"secretName": "app-secret"}},
		{"persistentVolumeClaim": map[string]any{"claimName": "app-data"}},
		{"projected": map[string]any{"sources": []any{map[string]any{"secret": map[string]any{"name": "app-secret"}}}}},
	} {
		volume := map[string]any{"name": "data"}
		for key, value := range source {
			volume[key] = value
		}
		if err := validateTenantVolume(volume); err != nil {
			t.Errorf("tenant-local volume rejected: %v: %v", source, err)
		}
	}
	for _, source := range []map[string]any{
		{"hostPath": map[string]any{"path": "/"}},
		{"nfs": map[string]any{"server": "other", "path": "/"}},
		{"csi": map[string]any{"driver": "rclone.csi.re8ch.com", "volumeAttributes": map[string]any{"remote": "other"}}},
		{"rbd": map[string]any{"pool": "other"}},
		{"persistentVolumeClaim": map[string]any{"claimName": "bad/name"}},
		{"projected": map[string]any{"sources": []any{map[string]any{"clusterTrustBundle": map[string]any{"name": "system"}}}}},
	} {
		volume := map[string]any{"name": "data"}
		for key, value := range source {
			volume[key] = value
		}
		if err := validateTenantVolume(volume); err == nil {
			t.Errorf("unmanaged workload volume accepted: %v", source)
		}
	}
	for _, kind := range []string{"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"} {
		pod := map[string]any{"volumes": []any{map[string]any{"name": "data", "nfs": map[string]any{"server": "other", "path": "/"}}}}
		spec := map[string]any{"template": map[string]any{"spec": pod}}
		if kind == "CronJob" {
			spec = map[string]any{"jobTemplate": map[string]any{"spec": spec}}
		}
		obj := map[string]any{"kind": kind, "spec": spec}
		if err := validateTenantObject(obj, "acme"); err == nil {
			t.Errorf("%s accepted unmanaged NFS volume", kind)
		}
	}
}

func TestTenantRoleBindingOnlyTargetsLocalServiceAccounts(t *testing.T) {
	roleRef := map[string]any{"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "reader"}
	for _, subject := range []map[string]any{
		{"kind": "User", "name": "admin@example.com", "apiGroup": "rbac.authorization.k8s.io"},
		{"kind": "Group", "name": "system:masters", "apiGroup": "rbac.authorization.k8s.io"},
		{"kind": "ServiceAccount", "name": "worker", "namespace": "other"},
		{"kind": "ServiceAccount", "name": "worker", "apiGroup": "rbac.authorization.k8s.io"},
		{"kind": "ServiceAccount", "name": "bad/name"},
	} {
		obj := map[string]any{"kind": "RoleBinding", "subjects": []any{subject}, "roleRef": roleRef}
		if err := validateTenantObject(obj, "acme"); err == nil {
			t.Fatalf("unsafe RoleBinding subject accepted: %v", subject)
		}
	}
	for _, subject := range []map[string]any{
		{"kind": "ServiceAccount", "name": "worker"},
		{"kind": "ServiceAccount", "name": "worker", "namespace": "acme"},
	} {
		obj := map[string]any{"kind": "RoleBinding", "subjects": []any{subject}, "roleRef": roleRef}
		if err := validateTenantObject(obj, "acme"); err != nil {
			t.Fatalf("local ServiceAccount binding rejected: %v", err)
		}
	}
	for _, ref := range []map[string]any{
		{"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "admin"},
		{"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "bad/name"},
		{"kind": "Role", "name": "reader"},
	} {
		obj := map[string]any{"kind": "RoleBinding", "subjects": []any{map[string]any{"kind": "ServiceAccount", "name": "worker"}}, "roleRef": ref}
		if err := validateTenantObject(obj, "acme"); err == nil {
			t.Fatalf("unsafe RoleBinding role reference accepted: %v", ref)
		}
	}
}

func TestTenantRoleCannotBypassSecretOrWorkloadBoundaries(t *testing.T) {
	for _, test := range []struct {
		group, resource, verb string
		allowed               bool
	}{
		{"", "configmaps", "get", true},
		{"", "pods/log", "get", true},
		{"apps", "deployments", "watch", true},
		{"", "secrets", "get", false},
		{"", "serviceaccounts/token", "create", false},
		{"", "pods/exec", "create", false},
		{"", "pods", "create", false},
		{"apps", "deployments", "patch", false},
		{"rbac.authorization.k8s.io", "roles", "get", false},
		{"", "*", "get", false},
		{"", "pods", "*", false},
	} {
		role := map[string]any{"kind": "Role", "rules": []any{map[string]any{
			"apiGroups": []any{test.group}, "resources": []any{test.resource}, "verbs": []any{test.verb},
		}}}
		err := validateTenantObject(role, "acme")
		if (err == nil) != test.allowed {
			t.Errorf("Role rule %s/%s %s allowed=%t: %v", test.group, test.resource, test.verb, test.allowed, err)
		}
	}
	role := map[string]any{"kind": "Role", "rules": []any{map[string]any{
		"apiGroups": []any{""}, "resources": []any{"pods"}, "verbs": []any{"get"}, "nonResourceURLs": []any{"/metrics"},
	}}}
	if err := validateTenantObject(role, "acme"); err == nil {
		t.Fatal("Role non-resource URL accepted")
	}
}

func TestTenantPVCRequiresDynamicSameNamespaceStorage(t *testing.T) {
	for _, class := range []string{"rbd-r640", "cephfs-re8ch", "rclone-s3"} {
		obj := map[string]any{"kind": "PersistentVolumeClaim", "spec": map[string]any{"storageClassName": class, "accessModes": []any{"ReadWriteOnce"}, "resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}}
		if err := validateTenantObject(obj, "acme"); err != nil {
			t.Fatalf("dynamic PVC with class %s rejected: %v", class, err)
		}
	}
	for _, spec := range []map[string]any{
		{"storageClassName": ""},
		{"storageClassName": "rbd-r640", "volumeName": "foreign-pv"},
		{"storageClassName": "cephfs-re8ch", "selector": map[string]any{"matchLabels": map[string]any{"tenant": "other"}}},
		{"storageClassName": "rbd-r640", "dataSourceRef": map[string]any{"kind": "PersistentVolumeClaim", "name": "copy", "namespace": "other"}},
		{"storageClassName": "rbd-r640", "dataSource": map[string]any{"kind": "PersistentVolumeClaim", "name": "copy", "namespace": "other"}},
	} {
		if err := validateTenantObject(map[string]any{"kind": "PersistentVolumeClaim", "spec": spec}, "acme"); err == nil {
			t.Fatalf("unsafe PVC accepted: %v", spec)
		}
	}
	localClone := map[string]any{"kind": "PersistentVolumeClaim", "spec": map[string]any{"storageClassName": "rbd-r640", "dataSourceRef": map[string]any{"kind": "PersistentVolumeClaim", "name": "source", "namespace": "acme"}}}
	if err := validateTenantObject(localClone, "acme"); err != nil {
		t.Fatalf("same-namespace clone rejected: %v", err)
	}
}

func TestTenantServiceAndWorkloadCannotBypassPublicEdge(t *testing.T) {
	for _, spec := range []map[string]any{
		{"type": "ClusterIP", "externalIPs": []any{"192.0.2.218"}},
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
	if err := validateExposureServiceSpec(map[string]any{"type": "ClusterIP", "selector": map[string]any{"app": "web"}, "ports": []any{map[string]any{"port": float64(80), "protocol": "TCP"}}}, 80); err != nil {
		t.Fatalf("selector-backed TCP Service was rejected: %v", err)
	}
	for _, spec := range []map[string]any{
		{"type": "ClusterIP", "ports": []any{map[string]any{"port": float64(80)}}},
		{"type": "ClusterIP", "selector": map[string]any{"app": "web"}, "ports": []any{map[string]any{"port": float64(80), "protocol": "UDP"}}},
		{"type": "NodePort", "selector": map[string]any{"app": "web"}, "ports": []any{map[string]any{"port": float64(80)}}},
	} {
		if err := validateExposureServiceSpec(spec, 80); err == nil {
			t.Fatalf("unroutable or unsafe Service was accepted: %v", spec)
		}
	}
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
