package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const maxTenantPublicEndpoints = 5

type exposureCreateInput struct {
	Namespace  string `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name       string `json:"name" jsonschema:"Managed exposure name"`
	Service    string `json:"service" jsonschema:"ClusterIP Service in the same namespace"`
	Port       int    `json:"port" jsonschema:"Service port from 1 through 65535"`
	Path       string `json:"path,omitempty" jsonschema:"Optional HTTP PathPrefix; defaults to /"`
	HealthPath string `json:"healthPath,omitempty" jsonschema:"Optional health-check path recorded for the managed edge controller"`
	Apply      bool   `json:"apply,omitempty" jsonschema:"Must be true to persist the exposure"`
}

type exposureReadInput struct {
	Namespace string `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name      string `json:"name,omitempty" jsonschema:"Optional managed exposure name"`
}

type exposureDeleteInput struct {
	Namespace string `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name      string `json:"name" jsonschema:"Managed exposure name"`
	Apply     bool   `json:"apply,omitempty" jsonschema:"Must be true to delete the exposure"`
}

func (s *server) registerExposureTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_exposure_create"), Name: "tenant_exposure_create", Description: "Preview or create a managed HTTPS exposure for a same-namespace ClusterIP Service through the platform PublicEdge. The Gateway, hostname and TLS policy remain platform-owned."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input exposureCreateInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.createExposure(ctx, id, input)
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_exposure_list"), Name: "tenant_exposure_list", Description: "List managed PublicEdge exposures in a tenant-owned namespace."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input exposureReadInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			input.Name = ""
			value, err := s.readExposures(ctx, id, input)
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_exposure_get"), Name: "tenant_exposure_get", Description: "Read one managed PublicEdge exposure and its non-secret Gateway status."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input exposureReadInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			if input.Name == "" {
				return nil, nil, errors.New("exposure name is required")
			}
			value, err := s.readExposures(ctx, id, input)
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_exposure_delete"), Name: "tenant_exposure_delete", Description: "Preview or delete a tenant-managed PublicEdge exposure. Operator or admin role and apply=true are required."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input exposureDeleteInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.deleteExposure(ctx, id, input)
			return nil, value, err
		})
}

func exposureHostname(tenant, name string) string {
	label := tenant + "-" + name
	if len(label) > 63 {
		digest := sha256.Sum256([]byte(label))
		label = strings.TrimRight(label[:54], "-") + "-" + hex.EncodeToString(digest[:])[:8]
	}
	return label + ".apps.re8ch.com"
}

func validHTTPPath(value string) bool {
	return strings.HasPrefix(value, "/") && !strings.ContainsAny(value, "\r\n") && len(value) <= 1024
}

func commandJSON(ctx context.Context, argv []string) (map[string]any, error) {
	result := commandResult(ctx, argv, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	var value map[string]any
	if err := json.Unmarshal([]byte(asString(result, "stdout")), &value); err != nil {
		return nil, errors.New("invalid Kubernetes API response")
	}
	return value, nil
}

func validateExposureService(ctx context.Context, namespace, service string, port int) error {
	value, err := commandJSON(ctx, []string{"kubectl", "--namespace", namespace, "get", "service", service, "-o", "json"})
	if err != nil {
		return err
	}
	spec := object(value["spec"])
	serviceType := asString(spec, "type")
	if serviceType != "" && serviceType != "ClusterIP" {
		return errors.New("managed exposure requires a ClusterIP Service")
	}
	for _, raw := range slice(spec["ports"]) {
		candidate := object(raw)
		if exposureIntValue(candidate["port"]) == port {
			return nil
		}
	}
	return errors.New("managed exposure port is not declared by the Service")
}

func exposureIntValue(value any) int {
	switch typed := value.(type) {
	case float64:
		return int(typed)
	case int:
		return typed
	case json.Number:
		parsed, _ := typed.Int64()
		return int(parsed)
	default:
		return 0
	}
}

func exposureItem(raw any, tenant, namespace string) (map[string]any, bool) {
	item := object(raw)
	metadata := object(item["metadata"])
	labels := object(metadata["labels"])
	if labels["tenancy.re8ch.com/managed-by"] != "tenant-exposure-controller" || labels["tenancy.re8ch.com/tenant"] != tenant {
		return nil, false
	}
	spec := object(item["spec"])
	rules := slice(spec["rules"])
	result := map[string]any{"name": metadata["name"], "namespace": namespace, "createdAt": metadata["creationTimestamp"], "resourceVersion": metadata["resourceVersion"], "hostnames": spec["hostnames"], "url": "https://" + firstString(spec["hostnames"]), "status": item["status"]}
	if len(rules) > 0 {
		rule := object(rules[0])
		result["matches"] = rule["matches"]
		result["backends"] = rule["backendRefs"]
	}
	if health := asString(object(metadata["annotations"]), "tenancy.re8ch.com/health-path"); health != "" {
		result["healthPath"] = health
	}
	return result, true
}

func firstString(value any) string {
	items := slice(value)
	if len(items) == 0 {
		return ""
	}
	text, _ := items[0].(string)
	return text
}

func (s *server) readExposures(ctx context.Context, id identity, input exposureReadInput) (any, error) {
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	argv := []string{"kubectl", "--namespace", namespace, "get", "httproutes.gateway.networking.k8s.io", "-l", "tenancy.re8ch.com/managed-by=tenant-exposure-controller,tenancy.re8ch.com/tenant=" + id.Tenant, "-o", "json"}
	if input.Name != "" {
		if !validKubernetesName(input.Name) {
			return nil, errors.New("invalid exposure name")
		}
		argv = []string{"kubectl", "--namespace", namespace, "get", "httproute.gateway.networking.k8s.io", input.Name, "-o", "json"}
	}
	decoded, err := commandJSON(ctx, argv)
	if err != nil {
		return nil, err
	}
	rawItems := slice(decoded["items"])
	if input.Name != "" {
		rawItems = []any{decoded}
	}
	items := make([]any, 0, len(rawItems))
	for _, raw := range rawItems {
		if item, ok := exposureItem(raw, id.Tenant, namespace); ok {
			items = append(items, item)
		}
	}
	if input.Name != "" && len(items) != 1 {
		return nil, errors.New("exposure is not owned by the authenticated tenant")
	}
	return map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "items": items}, nil
}

func (s *server) exposureCount(ctx context.Context, id identity) (int, error) {
	value, err := controlRequest(ctx, id.Tenant, "/v1/namespaces")
	if err != nil {
		return 0, err
	}
	total := 0
	for _, raw := range slice(value["items"]) {
		item := object(raw)
		spec, status := object(item["spec"]), object(item["status"])
		if spec["operation"] != "EnsurePresent" || status["phase"] != "Ready" {
			continue
		}
		namespace := asString(spec, "namespaceName")
		if namespace == "" {
			continue
		}
		listed, err := commandJSON(ctx, []string{"kubectl", "--namespace", namespace, "get", "httproutes.gateway.networking.k8s.io", "-l", "tenancy.re8ch.com/managed-by=tenant-exposure-controller,tenancy.re8ch.com/tenant=" + id.Tenant, "-o", "json"})
		if err != nil {
			return 0, err
		}
		total += len(slice(listed["items"]))
	}
	return total, nil
}

func (s *server) createExposure(ctx context.Context, id identity, input exposureCreateInput) (any, error) {
	if err := requireOperator(id); err != nil {
		return nil, err
	}
	if err := requireGrantedConsumable(ctx, id, "network.tenant.v1"); err != nil {
		return nil, err
	}
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	if !validKubernetesName(input.Name) || !validKubernetesName(input.Service) {
		return nil, errors.New("invalid exposure or Service name")
	}
	if input.Port < 1 || input.Port > 65535 {
		return nil, errors.New("invalid Service port")
	}
	if input.Path == "" {
		input.Path = "/"
	}
	if !validHTTPPath(input.Path) || (input.HealthPath != "" && !validHTTPPath(input.HealthPath)) {
		return nil, errors.New("invalid HTTP path")
	}
	if err := validateExposureService(ctx, namespace, input.Service, input.Port); err != nil {
		return nil, err
	}
	hostname := exposureHostname(id.Tenant, input.Name)
	annotations := map[string]any{}
	if input.HealthPath != "" {
		annotations["tenancy.re8ch.com/health-path"] = input.HealthPath
	}
	route := map[string]any{
		"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute",
		"metadata": map[string]any{"name": input.Name, "namespace": namespace, "labels": map[string]any{"tenancy.re8ch.com/tenant": id.Tenant, "tenancy.re8ch.com/managed-by": "tenant-exposure-controller"}, "annotations": annotations},
		"spec":     map[string]any{"parentRefs": []any{map[string]any{"name": "re8ch-gateway-canary", "namespace": "kube-system", "sectionName": "apps-wildcard-https"}}, "hostnames": []any{hostname}, "rules": []any{map[string]any{"matches": []any{map[string]any{"path": map[string]any{"type": "PathPrefix", "value": input.Path}}}, "backendRefs": []any{map[string]any{"name": input.Service, "port": input.Port}}}}},
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "name": input.Name, "url": "https://" + hostname, "applied": false, "object": route}
	if !input.Apply {
		return preview, nil
	}
	existingResult := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "get", "httproute.gateway.networking.k8s.io", input.Name, "-o", "json"}, nil, 120)
	if existingResult["exit_code"] == 0 {
		var existing map[string]any
		if json.Unmarshal([]byte(asString(existingResult, "stdout")), &existing) != nil {
			return nil, errors.New("invalid Kubernetes API response")
		}
		if _, owned := exposureItem(existing, id.Tenant, namespace); !owned {
			return nil, errors.New("an unmanaged or differently owned HTTPRoute already uses this name")
		}
	} else if strings.Contains(asString(existingResult, "stderr"), "NotFound") {
		count, err := s.exposureCount(ctx, id)
		if err != nil {
			return nil, err
		}
		if count >= maxTenantPublicEndpoints {
			return nil, fmt.Errorf("managed exposure maxPublicEndpoints quota of %d exceeded", maxTenantPublicEndpoints)
		}
	} else {
		return nil, errors.New(asString(existingResult, "stderr"))
	}
	serialized, _ := json.Marshal(route)
	result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "apply", "--server-side", "--field-manager=re8ch-tenant-exposure-controller", "-f", "-", "-o", "name"}, ptr(string(serialized)), 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	preview["applied"], preview["operationId"] = true, result["operation_id"]
	return preview, nil
}

func (s *server) deleteExposure(ctx context.Context, id identity, input exposureDeleteInput) (any, error) {
	if err := requireOperator(id); err != nil {
		return nil, err
	}
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	if !validKubernetesName(input.Name) {
		return nil, errors.New("invalid exposure name")
	}
	if _, err := s.readExposures(ctx, id, exposureReadInput{Namespace: namespace, Name: input.Name}); err != nil {
		return nil, err
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "name": input.Name, "deleted": false}
	if !input.Apply {
		return preview, nil
	}
	result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "delete", "httproute.gateway.networking.k8s.io", input.Name, "-o", "name"}, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	preview["deleted"], preview["operationId"] = true, result["operation_id"]
	return preview, nil
}
