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
	for _, tenant := range []string{"re8ch", "artchais"} {
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
		if !seen["registry.harbor.v1"] || !seen["observability.grafana.v1"] {
			t.Fatalf("live provider services missing for %s: %v", tenant, seen)
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
	if len(granted) != 1 || asString(granted[0], "serviceClass") != "registry.harbor.v1" {
		t.Fatalf("catalog ignored tenant grants or provider backing: %v", granted)
	}
}
