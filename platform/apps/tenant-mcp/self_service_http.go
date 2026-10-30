package main

import (
	"context"
	"errors"
	"net/http"
	"sort"
	"strings"
)

// The User Hub reads the same owner-scoped backend as MCP. A requested tenant
// is only a selector; authenticate verifies the caller's active membership.
func (s *server) selfServiceCapabilitiesHTTP(w http.ResponseWriter, r *http.Request) {
	billingHeaders(w, r)
	selected := r.Header.Get("X-RE8CH-Tenant")
	if selected == "" || !tenantName.MatchString(selected) {
		writeJSON(w, 400, map[string]string{"error": "select an organization"})
		return
	}
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	id, err := s.authenticate(r.Context(), strings.TrimPrefix(header, "Bearer "), selected)
	if errors.Is(err, errIdentityUnavailable) {
		writeJSON(w, 503, map[string]string{"error": "identity_unavailable"})
		return
	}
	if errors.Is(err, errInvalidToken) || id.Service {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	if err != nil || id.Tenant != selected {
		writeJSON(w, 403, map[string]string{"error": "organization access denied"})
		return
	}
	result, err := selfServiceCapabilities(r.Context(), id)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "tenant capability discovery unavailable"})
		return
	}
	writeJSON(w, 200, result)
}

// Shared by the browser and MCP discovery tool so both surfaces report the
// same active organization, namespace readiness, and supported operations.
func selfServiceCapabilities(ctx context.Context, id identity) (map[string]any, error) {
	namespaces, err := controlRequest(ctx, id.Tenant, "/v1/namespaces")
	if err != nil {
		return nil, err
	}
	grants, err := tenantGrantedConsumables(ctx, id.Tenant, id.Role)
	if err != nil {
		return nil, err
	}
	return selfServiceCapabilitiesFromState(id, namespaces, grants), nil
}

func selfServiceCapabilitiesFromState(id identity, namespaces map[string]any, grants []map[string]any) map[string]any {
	ready := readyTenantNamespaces(namespaces, id.Tenant)
	sort.Strings(ready)
	canWrite := id.Role == "operator" || id.Role == "admin"
	canExpose := canWrite && len(ready) > 0 && hasRequestableGrant(grants, "network.tenant.v1")
	canReadDatabaseBindings := len(ready) > 0 && hasRequestableGrant(grants, "database.postgresql.shared.v1")
	kubernetesTools := []string{"tenant_kubernetes_get", "tenant_kubernetes_apply", "tenant_kubernetes_delete"}
	namespacedWrite := canWrite && len(ready) > 0
	result := map[string]any{
		"tenantRef": id.Tenant, "role": id.Role, "readyNamespaces": ready, "grantedConsumables": grants,
		"capabilities": []map[string]any{
			{"id": "namespace", "kinds": []string{"Namespace"}, "tools": []string{"tenant_namespace_list", "tenant_namespace_create", "tenant_namespace_delete"}, "canWrite": canWrite},
			{"id": "workload", "kinds": []string{"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "configmap", "kinds": []string{"ConfigMap"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "service", "kinds": []string{"Service"}, "serviceTypes": []string{"ClusterIP"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "storage", "kinds": []string{"PersistentVolumeClaim"}, "tools": kubernetesTools, "requiredConsumable": "storage.volume.v1", "canWrite": namespacedWrite && hasRequestableGrant(grants, "storage.volume.v1")},
			{"id": "service-identity", "kinds": []string{"ServiceAccount", "Role", "RoleBinding"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "workload-policy", "kinds": []string{"HorizontalPodAutoscaler", "PodDisruptionBudget"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "runtime-observation", "kinds": []string{"Pod", "Event", "Endpoints", "ReplicaSet"}, "tools": []string{"tenant_kubernetes_get"}, "canWrite": false},
			{"id": "secret", "kinds": []string{"Secret"}, "writeOnly": true, "tools": []string{"tenant_secret_upsert", "tenant_secret_metadata_list", "tenant_secret_delete"}, "canWrite": canWrite && len(ready) > 0},
			{"id": "database-credential", "kinds": []string{"databasecredential"}, "tools": []string{"tenant_kubernetes_get"}, "queryKind": "databasecredential", "metadataOnly": true, "databaseState": "unverified", "requiredConsumable": "database.postgresql.shared.v1", "canRead": canReadDatabaseBindings, "canWrite": false},
			{"id": "exposure", "kinds": []string{"Managed HTTPRoute"}, "gatewayOwnership": "platform", "hostnameSuffix": ".apps.re8ch.com", "maxPublicEndpoints": maxTenantPublicEndpoints, "requiredConsumable": "network.tenant.v1", "tools": []string{"tenant_exposure_create", "tenant_exposure_list", "tenant_exposure_get", "tenant_exposure_delete"}, "canWrite": canExpose},
		},
	}
	if collaborationEnabled() {
		result["capabilities"] = append(result["capabilities"].([]map[string]any), map[string]any{
			"id": "collaboration", "requiredConsumable": "collaboration.stalwart.v1",
			"tools":      []string{"collaboration_connection", "mail_query", "mail_read", "calendar_query", "calendar_respond", "files_list", "files_read"},
			"canConnect": hasRequestableGrant(grants, "collaboration.stalwart.v1"), "providerState": "unverified", "ownership": "organization-and-user",
		})
	}
	return result
}

func tenantGrantedConsumables(ctx context.Context, tenant, role string) ([]map[string]any, error) {
	response, err := controlRequest(ctx, tenant, "/v1/capabilities")
	if err != nil {
		return nil, err
	}
	items, ok := response["grants"].([]any)
	if !ok || asString(response, "tenantRef") != tenant {
		return nil, errors.New("tenant grant discovery returned an invalid scope")
	}
	grants := make([]map[string]any, 0, len(items))
	for _, raw := range items {
		grant := object(raw)
		ref := asString(grant, "consumableRef")
		if ref == "" {
			continue
		}
		lifecycle := asString(grant, "lifecycle")
		requiredRole := asString(grant, "roleRef")
		canRequest := lifecycle == "Active" && (role == "admin" || role == "operator" && requiredRole != "admin")
		grants = append(grants, map[string]any{"name": asString(grant, "name"), "consumableRef": ref,
			"roleRef": requiredRole, "lifecycle": lifecycle, "canRequest": canRequest})
	}
	return grants, nil
}

func requireGrantedConsumable(ctx context.Context, id identity, capability string) error {
	grants, err := tenantGrantedConsumables(ctx, id.Tenant, id.Role)
	if err != nil {
		return err
	}
	if hasRequestableGrant(grants, capability) {
		return nil
	}
	return errors.New("active tenant grant and role are required for this capability")
}

func hasRequestableGrant(grants []map[string]any, capability string) bool {
	for _, grant := range grants {
		if grant["consumableRef"] == capability && grant["canRequest"] == true {
			return true
		}
	}
	return false
}
