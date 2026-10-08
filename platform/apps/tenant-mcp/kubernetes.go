package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var tenantApplyKinds = map[string]bool{
	"v1/ConfigMap": true, "v1/PersistentVolumeClaim": true, "v1/Service": true, "v1/ServiceAccount": true,
	"apps/v1/Deployment": true, "apps/v1/StatefulSet": true, "apps/v1/DaemonSet": true,
	"batch/v1/Job": true, "batch/v1/CronJob": true, "autoscaling/v2/HorizontalPodAutoscaler": true,
	"policy/v1/PodDisruptionBudget": true, "rbac.authorization.k8s.io/v1/Role": true, "rbac.authorization.k8s.io/v1/RoleBinding": true,
}

var tenantReadKinds = map[string]bool{
	"configmap": true, "persistentvolumeclaim": true, "service": true, "serviceaccount": true,
	"deployment": true, "statefulset": true, "daemonset": true, "job": true, "cronjob": true,
	"horizontalpodautoscaler": true, "poddisruptionbudget": true, "role": true, "rolebinding": true,
	"pod": true, "pods": true, "event": true, "events": true, "endpoint": true, "endpoints": true, "replicaset": true, "replicasets": true,
}

func tenantMutableKind(kind string) bool {
	for gvk := range tenantApplyKinds {
		if strings.EqualFold(gvk[strings.LastIndexByte(gvk, '/')+1:], kind) {
			return true
		}
	}
	return false
}

func (s *server) registerKubernetesTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"tenant_kubernetes_get", "tenant_kubernetes_apply", "tenant_kubernetes_delete"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(name), Name: name, Description: definition.Description, InputSchema: definition.InputSchema}, func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.callKubernetes(ctx, id, name, args)
			return nil, value, err
		})
	}
}

func readyNamespace(ctx context.Context, tenant, namespace string) error {
	value, err := controlRequest(ctx, tenant, "/v1/namespaces")
	if err != nil {
		return err
	}
	for _, raw := range slice(value["items"]) {
		item := object(raw)
		spec, status := object(item["spec"]), object(item["status"])
		if spec["tenantRef"] != tenant || spec["operation"] != "EnsurePresent" || status["phase"] != "Ready" || spec["namespaceName"] != namespace {
			continue
		}
		if expiration := asString(spec, "expiresAt"); expiration != "" {
			ends, err := parseTime(expiration)
			if err != nil || !ends.After(time.Now()) {
				continue
			}
		}
		return nil
	}
	return errors.New("namespace is not a Ready namespace owned by the authenticated tenant")
}

func validateTenantObject(obj map[string]any, namespace string) error {
	kind := asString(obj, "kind")
	metadata := object(obj["metadata"])
	if selected := asString(metadata, "namespace"); selected != "" && selected != namespace {
		return errors.New("object namespace is outside the authenticated tenant")
	}
	if kind == "RoleBinding" {
		for _, raw := range slice(obj["subjects"]) {
			subject := object(raw)
			if subject["kind"] == "ServiceAccount" {
				selected := asString(subject, "namespace")
				if selected != "" && selected != namespace {
					return errors.New("cross-namespace service account binding is denied")
				}
			}
		}
		if object(obj["roleRef"])["kind"] != "Role" {
			return errors.New("binding ClusterRoles through the tenant publication is denied")
		}
	}
	if kind == "Service" {
		service := object(obj["spec"])
		if service["type"] != nil && service["type"] != "" && service["type"] != "ClusterIP" {
			return errors.New("public or external service types require the shared PublicEdge service")
		}
		if len(slice(service["externalIPs"])) > 0 {
			return errors.New("Service externalIPs bypass the shared PublicEdge service")
		}
	}
	podSpec := object(obj["spec"])
	if kind == "CronJob" {
		podSpec = object(object(object(object(podSpec["jobTemplate"])["spec"])["template"])["spec"])
	} else if kind == "Deployment" || kind == "StatefulSet" || kind == "DaemonSet" || kind == "Job" {
		podSpec = object(object(podSpec["template"])["spec"])
	}
	for _, field := range []string{"hostNetwork", "hostPID", "hostIPC"} {
		if podSpec[field] == true {
			return errors.New("host namespaces are denied")
		}
	}
	for _, raw := range slice(podSpec["volumes"]) {
		if _, ok := object(raw)["hostPath"]; ok {
			return errors.New("hostPath volumes are denied")
		}
	}
	containers := append(slice(podSpec["containers"]), slice(podSpec["initContainers"])...)
	for _, raw := range containers {
		container := object(raw)
		security := object(container["securityContext"])
		if security["privileged"] == true || security["allowPrivilegeEscalation"] == true {
			return errors.New("privileged containers are denied")
		}
		for _, entry := range slice(container["ports"]) {
			if port := object(entry)["hostPort"]; port != nil && port != float64(0) && port != 0 {
				return errors.New("hostPort bypasses the shared PublicEdge service")
			}
		}
	}
	return nil
}

func labelTenantObject(obj map[string]any, tenant, namespace string) (map[string]any, error) {
	raw, err := json.Marshal(obj)
	if err != nil {
		return nil, err
	}
	var value map[string]any
	if err = json.Unmarshal(raw, &value); err != nil {
		return nil, err
	}
	kind := asString(value, "kind")
	if kind != "PersistentVolumeClaim" && kind != "Deployment" && kind != "StatefulSet" && kind != "DaemonSet" && kind != "Job" && kind != "CronJob" {
		return value, nil
	}
	metadata := object(value["metadata"])
	name := asString(metadata, "name")
	if name == "" {
		return nil, errors.New("metered native resources require metadata.name")
	}
	hash := sha256Hex(tenant + "/" + namespace + "/" + kind + "/" + name)[:32]
	labels := map[string]any{"tenant": tenant, "resource_ref": hash, "tenancy.re8ch.com/tenant": tenant, "birfucate.re8ch.com/metered": "true"}
	metaLabels := object(metadata["labels"])
	for key, item := range labels {
		metaLabels[key] = item
	}
	metadata["labels"] = metaLabels
	value["metadata"] = metadata
	if kind != "PersistentVolumeClaim" {
		spec := object(value["spec"])
		if kind == "CronJob" {
			jobTemplate := object(spec["jobTemplate"])
			jobSpec := object(jobTemplate["spec"])
			template := object(jobSpec["template"])
			templateMeta := object(template["metadata"])
			templateLabels := object(templateMeta["labels"])
			for key, item := range labels {
				templateLabels[key] = item
			}
			templateMeta["labels"] = templateLabels
			template["metadata"] = templateMeta
			jobSpec["template"] = template
			jobTemplate["spec"] = jobSpec
			spec["jobTemplate"] = jobTemplate
		} else {
			template := object(spec["template"])
			templateMeta := object(template["metadata"])
			templateLabels := object(templateMeta["labels"])
			for key, item := range labels {
				templateLabels[key] = item
			}
			templateMeta["labels"] = templateLabels
			template["metadata"] = templateMeta
			spec["template"] = template
		}
		value["spec"] = spec
	}
	return value, nil
}

func (s *server) callKubernetes(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	if name != "tenant_kubernetes_get" {
		if err := requireOperator(id); err != nil {
			return nil, err
		}
	}
	namespace := asString(args, "namespace")
	if namespace == "" {
		namespace = id.DefaultNamespace
	}
	if !validKubernetesName(namespace) {
		return nil, errors.New("invalid Kubernetes name")
	}
	if err := readyNamespace(ctx, id.Tenant, namespace); err != nil {
		return nil, err
	}
	if name == "tenant_kubernetes_get" {
		kind := strings.ToLower(asString(args, "kind"))
		if !tenantReadKinds[kind] {
			return nil, errors.New("kind is not in the non-secret tenant read allowlist")
		}
		argv := []string{"kubectl", "--namespace", namespace, "get", kind}
		if target := asString(args, "name"); target != "" {
			if !validKubernetesName(target) {
				return nil, errors.New("invalid Kubernetes name")
			}
			argv = append(argv, target)
		}
		argv = append(argv, "-o", "json")
		result := commandResult(ctx, argv, nil, 120)
		result["tenantRef"], result["namespace"], result["role"] = id.Tenant, namespace, id.Role
		return result, nil
	}
	if name == "tenant_kubernetes_apply" {
		obj := object(args["object"])
		if !tenantApplyKinds[asString(obj, "apiVersion")+"/"+asString(obj, "kind")] {
			return nil, errors.New("object kind is not in the tenant workload allowlist")
		}
		if err := validateTenantObject(obj, namespace); err != nil {
			return nil, err
		}
		metadata := object(obj["metadata"])
		metadata["namespace"] = namespace
		obj["metadata"] = metadata
		labeled, err := labelTenantObject(obj, id.Tenant, namespace)
		if err != nil {
			return nil, err
		}
		preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "role": id.Role, "applied": false, "object": labeled}
		if !flag(args, "apply", false) {
			return preview, nil
		}
		serialized, _ := json.Marshal(labeled)
		result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "apply", "--server-side", "--field-manager=re8ch-tenant-api", "-f", "-", "-o", "json"}, ptr(string(serialized)), 180)
		if result["exit_code"] != 0 {
			return nil, errors.New(asString(result, "stderr"))
		}
		preview["applied"], preview["result"] = true, result
		return preview, nil
	}
	kind := strings.ToLower(asString(args, "kind"))
	if !tenantMutableKind(kind) {
		return nil, errors.New("kind is not in the tenant workload allowlist")
	}
	target := asString(args, "name")
	if !validKubernetesName(target) {
		return nil, errors.New("invalid Kubernetes name")
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "role": id.Role, "deleted": false, "kind": kind, "name": target}
	if !flag(args, "apply", false) {
		return preview, nil
	}
	result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "delete", kind, target, "-o", "name"}, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	preview["deleted"], preview["result"] = true, result
	return preview, nil
}

func ptr(value string) *string { return &value }

func sha256Hex(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}
