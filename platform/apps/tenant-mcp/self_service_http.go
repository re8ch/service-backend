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
	ready := []string{}
	for _, raw := range slice(namespaces["items"]) {
		item := object(raw)
		spec, status := object(item["spec"]), object(item["status"])
		if spec["tenantRef"] == id.Tenant && spec["operation"] == "EnsurePresent" && status["phase"] == "Ready" {
			if namespace := asString(spec, "namespaceName"); namespace != "" {
				ready = append(ready, namespace)
			}
		}
	}
	sort.Strings(ready)
	canWrite := id.Role == "operator" || id.Role == "admin"
	kubernetesTools := []string{"tenant_kubernetes_get", "tenant_kubernetes_apply", "tenant_kubernetes_delete"}
	namespacedWrite := canWrite && len(ready) > 0
	return map[string]any{
		"tenantRef": id.Tenant, "role": id.Role, "readyNamespaces": ready,
		"capabilities": []map[string]any{
			{"id": "namespace", "kinds": []string{"Namespace"}, "tools": []string{"tenant_namespace_list", "tenant_namespace_create", "tenant_namespace_delete"}, "canWrite": canWrite},
			{"id": "workload", "kinds": []string{"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "configmap", "kinds": []string{"ConfigMap"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "service", "kinds": []string{"Service"}, "serviceTypes": []string{"ClusterIP"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "storage", "kinds": []string{"PersistentVolumeClaim"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "service-identity", "kinds": []string{"ServiceAccount", "Role", "RoleBinding"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "workload-policy", "kinds": []string{"HorizontalPodAutoscaler", "PodDisruptionBudget"}, "tools": kubernetesTools, "canWrite": namespacedWrite},
			{"id": "runtime-observation", "kinds": []string{"Pod", "Event", "Endpoints", "ReplicaSet"}, "tools": []string{"tenant_kubernetes_get"}, "canWrite": false},
			{"id": "secret", "kinds": []string{"Secret"}, "writeOnly": true, "tools": []string{"tenant_secret_upsert", "tenant_secret_metadata_list", "tenant_secret_delete"}, "canWrite": canWrite && len(ready) > 0},
			{"id": "exposure", "kinds": []string{"Managed HTTPRoute"}, "gatewayOwnership": "platform", "hostnameSuffix": ".apps.re8ch.com", "maxPublicEndpoints": maxTenantPublicEndpoints, "tools": []string{"tenant_exposure_create", "tenant_exposure_list", "tenant_exposure_get", "tenant_exposure_delete"}, "canWrite": canWrite && len(ready) > 0},
		},
	}, nil
}
