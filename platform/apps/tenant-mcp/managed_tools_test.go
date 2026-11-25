package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func managedFixture(tenant string) map[string]any {
	return map[string]any{
		"activeOrgRef": tenant, "ownerOrgRef": "publisher-org", "state": "Ready",
		"publicationRef": "toolpub_observer1", "revisionRef": "toolrev_0123456789abcdef0123456789abcdef",
		"installationRef":    "toolins_0123456789abcdef0123456789abcdef",
		"bindingRef":         "toolbind_0123456789abcdef0123456789abcdef",
		"serviceInstanceRef": "service://" + tenant + "/observer", "serviceBindingRef": "binding://" + tenant + "/observer",
		"toolRef": "https://observer.example/mcp", "slug": "observer", "displayName": "Observer", "providerKind": "remote-mcp",
	}
}

func TestManagedToolCompatibilityProjectionUsesAuthenticatedActiveOrg(t *testing.T) {
	tenant := "personal-11111111-1111-4111-8111-111111111111"
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Tenant-Ref") != tenant || r.Header.Get("X-Tenant-Caller") != "tenant" || r.Header.Get("X-Tenant-Service-Token") != "claim-secret" {
			t.Fatalf("managed catalog was not bound to authenticated tenant headers: %v", r.Header)
		}
		switch r.URL.Path {
		case "/v1/capabilities":
			writeJSON(w, 200, map[string]any{"tenantRef": tenant, "grants": []any{}})
		case "/v1/services":
			writeJSON(w, 200, map[string]any{"items": []any{}})
		case "/v1/tools/catalog":
			writeJSON(w, 200, map[string]any{"activeOrgRef": tenant, "items": []any{managedFixture(tenant)}})
		default:
			writeJSON(w, 404, map[string]string{"error": "not_found"})
		}
	}))
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	s := testServer(t)
	var err error
	s.ledger, err = openLedger(t.TempDir() + "/ledger.db")
	if err != nil {
		t.Fatal(err)
	}
	defer s.ledger.Close()
	id := identity{Subject: "user-1", Tenant: tenant, OrganizationRef: tenant, RuntimeTenantRef: "personal-standard", Role: "admin"}
	ctx := context.WithValue(context.Background(), identityKey{}, id)

	catalogValue, err := s.callService(ctx, id, "service_catalog_list", map[string]any{"tenantRef": "attacker-org"})
	if err != nil {
		t.Fatal(err)
	}
	catalog := catalogValue.(map[string]any)
	services := catalog["services"].([]map[string]any)
	var observer map[string]any
	for _, service := range services {
		if service["name"] == "observer" {
			observer = service
		}
	}
	if catalog["tenantRef"] != tenant || len(services) != 3 || observer == nil || observer["serviceClass"] != managedMCPServiceClass {
		t.Fatalf("managed catalog projection is wrong: %#v", catalog)
	}

	instancesValue, err := s.callService(ctx, id, "service_instance_list", map[string]any{"tenantRef": "attacker-org"})
	if err != nil {
		t.Fatal(err)
	}
	instances := instancesValue.(map[string]any)["instances"].([]any)
	if len(instances) != 1 || object(instances[0])["tenant_ref"] != tenant || object(instances[0])["service_class"] != managedMCPServiceClass {
		t.Fatalf("managed instance projection is wrong: %#v", instances)
	}

	bindingsValue, err := s.callService(ctx, id, "service_binding_list", map[string]any{"resource_ref": "service://" + tenant + "/observer", "tenantRef": "attacker-org"})
	if err != nil {
		t.Fatal(err)
	}
	bindings := bindingsValue.(map[string]any)["bindings"].([]map[string]any)
	if len(bindings) != 1 || bindings[0]["tenantRef"] != tenant || bindings[0]["credentialMaterialReturned"] != false {
		t.Fatalf("managed binding projection is wrong: %#v", bindings)
	}
}

func TestManagedToolCatalogFailsClosedOnCrossOrgOrMalformedRows(t *testing.T) {
	for name, response := range map[string]map[string]any{
		"cross-org-envelope": {"activeOrgRef": "other", "items": []any{}},
		"cross-org-row": {"activeOrgRef": "acme", "items": []any{func() map[string]any {
			item := managedFixture("other")
			return item
		}()}},
		"invalid-tool-url": {"activeOrgRef": "acme", "items": []any{func() map[string]any {
			item := managedFixture("acme")
			item["toolRef"] = "not-a-url"
			return item
		}()}},
	} {
		t.Run(name, func(t *testing.T) {
			control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(response)
			}))
			defer control.Close()
			t.Setenv("TENANT_CONTROL_URL", control.URL)
			t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
			if _, err := managedToolInstallations(context.Background(), "acme"); err == nil {
				t.Fatal("unsafe managed tool catalog was accepted")
			}
		})
	}
}

func TestManagedToolCatalog404IsBackwardCompatible(t *testing.T) {
	control := httptest.NewServer(http.NotFoundHandler())
	defer control.Close()
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	items, err := managedToolInstallations(context.Background(), "acme")
	if err != nil || len(items) != 0 {
		t.Fatalf("missing compatibility endpoint must preserve the 1.0.2 static catalog: %v %#v", err, items)
	}
}
