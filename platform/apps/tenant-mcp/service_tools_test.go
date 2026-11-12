package main

import (
	"strings"
	"testing"
)

func TestServiceCatalogOnlyAdvertisesLiveProvisioners(t *testing.T) {
	catalog, err := serviceCatalog()
	if err != nil {
		t.Fatal(err)
	}
	for _, tenant := range []string{"admin", "re8ch", "artchais"} {
		services := liveServiceOnly(catalog, tenant)
		seen := map[string]bool{}
		for _, service := range services {
			name := asString(service, "serviceClass")
			if !providerBacked[name] {
				t.Fatalf("intent-only service %s advertised to %s", name, tenant)
			}
			seen[name] = true
		}
		if seen["network.among-clusters.v1"] != (tenant == "artchais") {
			t.Fatalf("tenant-specific service leaked or disappeared for %s: %v", tenant, seen)
		}
		if !seen["registry.harbor.v1"] || !seen["observability.grafana.v1"] || !seen["application.ledger.v1"] {
			t.Fatalf("live provider services missing for %s: %v", tenant, seen)
		}
		for _, serviceClass := range []string{"ai.model-gateway.v1", "collaboration.stalwart.v1", "sandbox.runtime.v1"} {
			if !seen[serviceClass] {
				t.Fatalf("provider control service %s missing for %s: %v", serviceClass, tenant, seen)
			}
		}
		if seen["integration.github.enterprise-governance.v1"] != (tenant == "admin") {
			t.Fatalf("Admin-only GitHub governance service leaked or disappeared for %s: %v", tenant, seen)
		}
	}
	if err := noLiveServiceProvisioner("storage.volume.v1"); err == nil || !strings.Contains(err.Error(), "tenant_claim_plan") {
		t.Fatalf("volume did not direct the caller to its live claim interface: %v", err)
	}
	if err := noLiveServiceProvisioner("database.postgresql.shared.v1"); err == nil || !strings.Contains(err.Error(), "no live provisioner") {
		t.Fatalf("unprovisioned database was not rejected: %v", err)
	}
	granted := activeGrantedServices(liveServiceOnly(catalog, "re8ch"), []map[string]any{
		{"consumableRef": "registry.harbor.v1", "lifecycle": "Active"},
		{"consumableRef": "observability.grafana.v1", "lifecycle": "Suspended"},
		{"consumableRef": "database.postgresql.shared.v1", "lifecycle": "Active"},
	})
	if len(granted) != 2 || asString(granted[0], "serviceClass") != "database.postgresql.shared.v1" || asString(granted[1], "serviceClass") != "registry.harbor.v1" {
		t.Fatalf("catalog ignored tenant grants or provider backing: %v", granted)
	}
}

func TestAgentProviderBindingsExposeOnlyOpaqueReferences(t *testing.T) {
	instance := func(class, phase, access string) any {
		return map[string]any{
			"metadata": map[string]any{"name": strings.ReplaceAll(class, ".", "-"), "uid": "uid-" + class, "generation": float64(1)},
			"spec":     map[string]any{"tenantRef": "re8ch", "serviceClass": class},
			"status":   map[string]any{"phase": phase, "observedGeneration": float64(1), "access": map[string]any{"accessRef": access}},
		}
	}
	items := []any{
		instance("ai.model-gateway.v1", "Ready", "credential://litellm/re8ch/key"),
		instance("collaboration.stalwart.v1", "AuthorizationRequired", "account://stalwart/re8ch/account"),
		instance("sandbox.runtime.v1", "Ready", "capability://cubesandbox/re8ch/runtime"),
		instance("ai.model-gateway.v1", "Ready", "credential://litellm/other/cross-tenant"),
	}
	got := providerBindings(items, "re8ch", "")
	if len(got) != 3 {
		t.Fatalf("agent provider bindings were not safely projected: %v", got)
	}
	for _, binding := range got {
		if binding["credentialMaterialReturned"] != false || binding["accessRef"] == "" {
			t.Fatalf("provider binding exposed credential material: %v", binding)
		}
	}
}

func TestProviderBindingsOnlyExposeCurrentOwnedOpaqueAccess(t *testing.T) {
	instance := func(tenant, phase, credential string, observed float64) any {
		return map[string]any{
			"metadata": map[string]any{"name": "registry", "uid": "uid-1", "generation": float64(2)},
			"spec":     map[string]any{"tenantRef": tenant, "serviceClass": "registry.harbor.v1"},
			"status":   map[string]any{"phase": phase, "observedGeneration": observed, "humanCredentialRef": credential},
		}
	}
	items := []any{
		instance("acme", "Ready", "credential://harbor/acme/opaque", 2),
		instance("other", "Ready", "credential://harbor/other/opaque", 2),
		instance("acme", "Suspended", "credential://harbor/acme/stale", 2),
		instance("acme", "Ready", "credential://harbor/acme/unreconciled", 1),
		instance("acme", "Ready", "credential://harbor/other/cross-tenant", 2),
	}
	bindings := providerBindings(items, "acme", "service://acme/registry")
	if len(bindings) != 1 || bindings[0]["accessRef"] != "credential://harbor/acme/opaque" || bindings[0]["revocable"] != false {
		t.Fatalf("provider binding projection was unsafe: %v", bindings)
	}
	if len(providerBindings(items, "acme", "service://acme/unrelated")) != 0 {
		t.Fatal("resource filter was ignored")
	}
	ledger := map[string]any{
		"metadata": map[string]any{"name": "ledger", "uid": "uid-ledger", "generation": float64(1)},
		"spec":     map[string]any{"tenantRef": "admin", "serviceClass": "application.ledger.v1"},
		"status": map[string]any{"phase": "Ready", "observedGeneration": float64(1),
			"access": map[string]any{"endpointRef": "https://cn.ledger.re8ch.com"}},
	}
	ledgerBindings := providerBindings([]any{ledger}, "admin", "service://admin/ledger")
	if len(ledgerBindings) != 1 || ledgerBindings[0]["bindingType"] != "endpoint:oauth" ||
		ledgerBindings[0]["endpointRef"] != "https://cn.ledger.re8ch.com" {
		t.Fatalf("Ledger endpoint binding was not projected: %v", ledgerBindings)
	}

	machine := map[string]any{
		"metadata": map[string]any{"name": "build-registry", "uid": "uid-2", "generation": float64(3)},
		"spec":     map[string]any{"tenantRef": "acme", "serviceClass": "registry.harbor.v1"},
		"status": map[string]any{
			"phase": "Ready", "observedGeneration": float64(3),
			"access": map[string]any{"robotCredentialRef": "credential://harbor/acme/machine"},
		},
	}
	machineBindings := providerBindings([]any{machine}, "acme", "service://acme/build-registry")
	if len(machineBindings) != 1 || machineBindings[0]["accessRef"] != "credential://harbor/acme/machine" {
		t.Fatalf("machine Harbor credential was not projected: %v", machineBindings)
	}

	github := map[string]any{
		"metadata": map[string]any{"name": "github-enterprise-governance", "uid": "uid-github", "generation": float64(1)},
		"spec":     map[string]any{"tenantRef": "admin", "serviceClass": "integration.github.enterprise-governance.v1"},
		"status": map[string]any{"phase": "Ready", "observedGeneration": float64(1), "access": map[string]any{
			"accessRef": "capability://github-enterprise-governance/admin/default",
		}},
	}
	githubBindings := providerBindings([]any{github}, "admin", "service://admin/github-enterprise-governance")
	if len(githubBindings) != 1 || githubBindings[0]["accessRef"] != "capability://github-enterprise-governance/admin/default" || githubBindings[0]["credentialMaterialReturned"] != false {
		t.Fatalf("GitHub governance binding exposed an unsafe contract: %v", githubBindings)
	}
	if len(providerBindings([]any{github}, "re8ch", "")) != 0 {
		t.Fatal("admin GitHub governance binding leaked to a tenant")
	}
}
