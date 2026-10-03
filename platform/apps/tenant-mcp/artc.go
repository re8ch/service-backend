package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var artcReadResources = map[string]bool{"namespaces": true, "nodes": true, "pods": true, "pods/log": true, "services": true, "endpoints": true, "events": true, "persistentvolumeclaims": true, "deployments": true, "statefulsets": true, "daemonsets": true, "replicasets": true, "jobs": true, "cronjobs": true, "ingresses": true, "httproutes": true, "networkpolicies": true, "storageclasses": true}
var artcApplyKinds = map[string]bool{"v1/Service": true, "v1/PersistentVolumeClaim": true, "apps/v1/Deployment": true, "apps/v1/StatefulSet": true, "batch/v1/Job": true, "batch/v1/CronJob": true, "networking.k8s.io/v1/Ingress": true, "networking.k8s.io/v1/NetworkPolicy": true, "gateway.networking.k8s.io/v1/HTTPRoute": true}
var artcDeniedWords = map[string]bool{"secret": true, "secrets": true, "serviceaccounts/token": true, "exec": true, "attach": true, "cp": true, "debug": true, "proxy": true, "port-forward": true, "auth": true, "config": true, "certificate": true, "token": true, "raw": true}
var artcArgParts = regexp.MustCompile(`[./=,:]+`)
var artcSensitiveInput = regexp.MustCompile(`(?i)(kind\s*:\s*secret|stringData\s*:|client-key-data|token\s*:|password\s*:)`)

func artcTenant() string {
	value := os.Getenv("ARTCHAIS_TENANT_REF")
	if value == "" {
		return "artchais"
	}
	return value
}

func artcKubeconfig(role string) (string, error) {
	dir := os.Getenv("KUBECONFIG_DIR")
	if dir == "" {
		dir = "/etc/re8ch-ai-operations/kubeconfigs"
	}
	if role != "viewer" && role != "operator" && role != "admin" {
		role = "viewer"
	}
	path := filepath.Join(dir, "artc-"+role+".yaml")
	if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() {
		return "", errors.New("artc " + role + " access grant is unavailable")
	}
	return path, nil
}

func containsSensitiveField(value any) bool {
	sensitive := map[string]bool{"secret": true, "password": true, "token": true, "privatekey": true, "clientsecret": true, "apikey": true, "stringdata": true}
	switch typed := value.(type) {
	case map[string]any:
		for key, item := range typed {
			normalized := strings.ReplaceAll(strings.ToLower(key), "_", "")
			if sensitive[normalized] || containsSensitiveField(item) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if containsSensitiveField(item) {
				return true
			}
		}
	}
	return false
}

func forbiddenAdminArgv(values []string, stdin string) bool {
	flags := []string{"kubeconfig", "client-key", "client-certificate", "certificate-authority", "--token", "--server", "--as"}
	for _, raw := range values {
		value := strings.ToLower(raw)
		for _, flag := range flags {
			if strings.Contains(value, flag) {
				return true
			}
		}
		for _, part := range artcArgParts.Split(strings.TrimLeft(value, "-"), -1) {
			if artcDeniedWords[part] {
				return true
			}
		}
	}
	return stdin != "" && artcSensitiveInput.MatchString(stdin)
}

func (s *server) registerArtcTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"artc_kubernetes_read", "artc_kubernetes_apply", "artc_kubernetes_admin"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(name), Name: name, Description: definition.Description, InputSchema: definition.InputSchema}, func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.callArtc(ctx, id, name, args)
			return nil, value, err
		})
	}
}

func (s *server) callArtc(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	tenant := artcTenant()
	if id.Tenant != tenant {
		return nil, errors.New("this publication is restricted to the Artchais tenant")
	}
	role := id.Role
	if name == "artc_kubernetes_read" {
		resource := strings.ToLower(asString(args, "resource"))
		if !artcReadResources[resource] {
			return nil, errors.New("resource is not in the non-secret read allowlist")
		}
		kubeconfig, err := artcKubeconfig(role)
		if err != nil {
			return nil, err
		}
		argv := []string{"kubectl", "--kubeconfig", kubeconfig}
		if namespace := asString(args, "namespace"); namespace != "" {
			if !validKubernetesName(namespace) {
				return nil, errors.New("invalid Kubernetes name")
			}
			argv = append(argv, "--namespace", namespace)
		}
		argv = append(argv, "get", resource)
		if target := asString(args, "name"); target != "" {
			if !validKubernetesName(target) {
				return nil, errors.New("invalid Kubernetes name")
			}
			argv = append(argv, target)
		}
		argv = append(argv, "-o", "json")
		result := commandResult(ctx, argv, nil, 120)
		result["tenantRef"], result["role"] = tenant, role
		return result, nil
	}
	if name == "artc_kubernetes_apply" {
		if err := requireOperator(id); err != nil {
			return nil, err
		}
		obj := object(args["object"])
		if !artcApplyKinds[asString(obj, "apiVersion")+"/"+asString(obj, "kind")] {
			return nil, errors.New("object kind is not in the Artchais workload allowlist")
		}
		metadata := object(obj["metadata"])
		if selected := asString(metadata, "namespace"); selected != "" && selected != tenant {
			return nil, errors.New("objects must target the Artchais namespace")
		}
		objCopy := map[string]any{}
		for key, value := range obj {
			objCopy[key] = value
		}
		metaCopy := map[string]any{}
		for key, value := range metadata {
			metaCopy[key] = value
		}
		metaCopy["namespace"] = tenant
		objCopy["metadata"] = metaCopy
		if containsSensitiveField(objCopy) {
			return nil, errors.New("inline credential and Secret fields are denied")
		}
		preview := map[string]any{"tenantRef": tenant, "role": role, "applied": false, "object": objCopy}
		if !flag(args, "apply", false) {
			return preview, nil
		}
		kubeconfig, err := artcKubeconfig(role)
		if err != nil {
			return nil, err
		}
		serialized, _ := json.Marshal(objCopy)
		result := commandResult(ctx, []string{"kubectl", "--kubeconfig", kubeconfig, "--namespace", tenant, "apply", "-f", "-", "-o", "json"}, ptr(string(serialized)), 180)
		preview["applied"], preview["result"] = true, result
		return preview, nil
	}
	if role != "admin" {
		return nil, errors.New("admin role is required")
	}
	values := []string{}
	for _, raw := range slice(args["argv"]) {
		value, ok := raw.(string)
		if !ok || value == "" || len(value) > 1024 {
			return nil, errors.New("argv must contain bounded non-empty strings")
		}
		values = append(values, value)
	}
	if len(values) == 0 || len(values) > 128 {
		return nil, errors.New("argv must contain bounded non-empty strings")
	}
	stdin := asString(args, "stdin")
	if forbiddenAdminArgv(values, stdin) {
		return nil, errors.New("credential export, Secret access, interactive pod access and proxy commands are denied")
	}
	preview := map[string]any{"tenantRef": tenant, "role": role, "applied": false, "argv": values}
	if !flag(args, "apply", false) {
		return preview, nil
	}
	kubeconfig, err := artcKubeconfig("admin")
	if err != nil {
		return nil, err
	}
	input := (*string)(nil)
	if stdin != "" {
		input = &stdin
	}
	timeout := intValue(args["timeout_seconds"])
	if timeout == 0 {
		timeout = 300
	}
	result := commandResult(ctx, append([]string{"kubectl", "--kubeconfig", kubeconfig}, values...), input, timeout)
	preview["applied"], preview["result"] = true, result
	return preview, nil
}
