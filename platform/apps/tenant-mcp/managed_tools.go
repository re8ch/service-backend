package main

import (
	"context"
	"errors"
	"net/url"
)

const managedMCPServiceClass = "integration.mcp.managed.v1"

type managedToolInstallation struct {
	ActiveOrgRef       string
	OwnerOrgRef        string
	PublicationRef     string
	RevisionRef        string
	InstallationRef    string
	BindingRef         string
	ServiceInstanceRef string
	ServiceBindingRef  string
	ToolRef            string
	Slug               string
	DisplayName        string
	ProviderKind       string
}

// managedToolInstallations consumes the tenant-control compatibility contract.
// The controller, not prompt arguments, derives activeOrgRef from the
// authenticated X-Tenant-Ref header added by controlRequest.
func managedToolInstallations(ctx context.Context, tenant string) ([]managedToolInstallation, error) {
	response, err := controlRequest(ctx, tenant, "/v1/tools/catalog")
	if err != nil {
		var unavailable controlHTTPError
		if errors.As(err, &unavailable) && unavailable.Status == 404 {
			return []managedToolInstallation{}, nil
		}
		return nil, err
	}
	items, ok := response["items"].([]any)
	if response["activeOrgRef"] == nil && response["items"] == nil {
		// A pre-contract tenant-control may route the unknown path to a generic
		// compatibility response. Treat only an entirely absent envelope as the
		// old endpoint; once either scope field is present validation is strict.
		return []managedToolInstallation{}, nil
	}
	if !ok || asString(response, "activeOrgRef") != tenant {
		return nil, errors.New("managed tool catalog returned an invalid organization scope")
	}
	result := make([]managedToolInstallation, 0, len(items))
	seen := map[string]bool{}
	for _, raw := range items {
		item := object(raw)
		toolRef := asString(item, "toolRef")
		parsed, parseErr := url.Parse(toolRef)
		value := managedToolInstallation{
			ActiveOrgRef: tenant, OwnerOrgRef: asString(item, "ownerOrgRef"),
			PublicationRef: asString(item, "publicationRef"), RevisionRef: asString(item, "revisionRef"),
			InstallationRef: asString(item, "installationRef"), BindingRef: asString(item, "bindingRef"),
			ServiceInstanceRef: asString(item, "serviceInstanceRef"), ServiceBindingRef: asString(item, "serviceBindingRef"),
			ToolRef: toolRef, Slug: asString(item, "slug"), DisplayName: asString(item, "displayName"),
			ProviderKind: asString(item, "providerKind"),
		}
		if asString(item, "activeOrgRef") != tenant || asString(item, "state") != "Ready" ||
			parseErr != nil || parsed.Scheme == "" || parsed.Host == "" || value.OwnerOrgRef == "" ||
			value.PublicationRef == "" || value.RevisionRef == "" || value.InstallationRef == "" ||
			value.BindingRef == "" || value.ServiceInstanceRef == "" || value.ServiceBindingRef == "" ||
			value.Slug == "" || value.DisplayName == "" || seen[value.BindingRef] {
			return nil, errors.New("managed tool catalog returned an invalid ready installation")
		}
		seen[value.BindingRef] = true
		result = append(result, value)
	}
	return result, nil
}

func (item managedToolInstallation) catalogProjection() map[string]any {
	return map[string]any{
		"serviceClass": managedMCPServiceClass, "name": item.Slug, "displayName": item.DisplayName,
		"publicationRef": item.PublicationRef, "revisionRef": item.RevisionRef,
		"installationRef": item.InstallationRef, "toolRef": item.ToolRef,
		"plans": []any{"installed"}, "regions": []any{"managed"}, "parameters": []any{},
		"adapter": "managed-mcp", "accessUnit": "OAuthToolBinding", "bindings": []any{"mcp:oauth"},
	}
}

func (item managedToolInstallation) instanceProjection(tenant string) map[string]any {
	return map[string]any{
		"resource_ref": item.ServiceInstanceRef, "tenant_ref": tenant, "name": item.Slug,
		"service_class": managedMCPServiceClass, "state": "Ready", "generation": 1,
		"spec": map[string]any{"serviceClass": managedMCPServiceClass, "planRef": "installed", "regionRef": "managed",
			"publicationRef": item.PublicationRef, "revisionRef": item.RevisionRef, "installationRef": item.InstallationRef,
			"ownerOrgRef": item.OwnerOrgRef, "toolRef": item.ToolRef, "providerKind": item.ProviderKind},
		"source": "billing-ready-tool-installation",
	}
}

func (item managedToolInstallation) bindingProjection(tenant string) map[string]any {
	return map[string]any{
		"bindingRef": item.BindingRef, "resourceRef": item.ServiceInstanceRef,
		"serviceBindingRef": item.ServiceBindingRef, "serviceClass": managedMCPServiceClass,
		"bindingType": "mcp:oauth", "toolRef": item.ToolRef, "tenantRef": tenant,
		"ownerOrgRef": item.OwnerOrgRef, "providerKind": item.ProviderKind,
		"state": "Ready", "source": "billing-ready-tool-installation",
		"revocable": false, "credentialMaterialReturned": false,
	}
}
