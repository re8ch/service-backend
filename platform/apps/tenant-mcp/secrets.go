package main

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"sort"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var secretKey = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

type secretUpsertInput struct {
	Namespace string            `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name      string            `json:"name" jsonschema:"Kubernetes Secret name"`
	Values    map[string]string `json:"values" jsonschema:"Write-only Secret key/value pairs; values are never returned"`
	Apply     bool              `json:"apply,omitempty" jsonschema:"Must be true to persist the Secret; false returns a value-free preview"`
}

type secretMetadataInput struct {
	Namespace string `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name      string `json:"name,omitempty" jsonschema:"Optional Secret name"`
}

type secretDeleteInput struct {
	Namespace string `json:"namespace,omitempty" jsonschema:"Ready namespace owned by the authenticated tenant; defaults to the tenant default namespace"`
	Name      string `json:"name" jsonschema:"Kubernetes Secret name"`
	Apply     bool   `json:"apply,omitempty" jsonschema:"Must be true to delete the Secret"`
}

func (s *server) registerSecretTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_secret_upsert"), Name: "tenant_secret_upsert", Description: "Preview or write a tenant-owned Kubernetes Secret. Secret values are accepted write-only and never returned. Operator or admin role and apply=true are required."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input secretUpsertInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.upsertSecret(ctx, id, input)
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_secret_metadata_list"), Name: "tenant_secret_metadata_list", Description: "List metadata and key names for Secrets written through the tenant Secret broker. Secret values are never read or returned."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input secretMetadataInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.secretMetadata(ctx, id, input)
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_secret_delete"), Name: "tenant_secret_delete", Description: "Preview or delete a Secret written through the tenant Secret broker. Operator or admin role and apply=true are required."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input secretDeleteInput) (*mcp.CallToolResult, any, error) {
			id, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.deleteSecret(ctx, id, input)
			return nil, value, err
		})
}

func tenantNamespace(ctx context.Context, id identity, selected string) (string, error) {
	if selected == "" {
		selected = id.DefaultNamespace
	}
	if !validKubernetesName(selected) {
		return "", errors.New("invalid Kubernetes namespace")
	}
	if err := readyNamespace(ctx, id.Tenant, selected); err != nil {
		return "", err
	}
	return selected, nil
}

func secretKeys(values map[string]string) ([]string, error) {
	if len(values) == 0 {
		return nil, errors.New("at least one Secret value is required")
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		if len(key) > 253 || !secretKey.MatchString(key) {
			return nil, errors.New("invalid Secret key")
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys, nil
}

func (s *server) upsertSecret(ctx context.Context, id identity, input secretUpsertInput) (any, error) {
	if err := requireOperator(id); err != nil {
		return nil, err
	}
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	if !validKubernetesName(input.Name) {
		return nil, errors.New("invalid Kubernetes Secret name")
	}
	keys, err := secretKeys(input.Values)
	if err != nil {
		return nil, err
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "name": input.Name, "keys": keys, "applied": false}
	if !input.Apply {
		return preview, nil
	}
	resource := map[string]any{
		"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
		"metadata": map[string]any{"name": input.Name, "namespace": namespace, "labels": map[string]any{
			"tenancy.re8ch.com/tenant": id.Tenant, "tenancy.re8ch.com/managed-by": "tenant-secret-broker",
		}},
		"stringData": input.Values,
	}
	serialized, _ := json.Marshal(resource)
	result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "apply", "--server-side", "--field-manager=re8ch-tenant-secret-broker", "-f", "-", "-o", "name"}, ptr(string(serialized)), 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	preview["applied"] = true
	preview["operationId"] = result["operation_id"]
	return preview, nil
}

func (s *server) secretMetadata(ctx context.Context, id identity, input secretMetadataInput) (any, error) {
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	// Render only allowlisted metadata and map keys in the kubectl subprocess.
	// Secret data values never enter this process, its logs, or its MCP result.
	template := `{{range .items}}{{.metadata.name}}{{"\t"}}{{.metadata.resourceVersion}}{{"\t"}}{{.metadata.creationTimestamp}}{{"\t"}}{{range $key, $_ := .data}}{{$key}}{{","}}{{end}}{{"\n"}}{{end}}`
	argv := []string{"kubectl", "--namespace", namespace, "get", "secrets", "-l", "tenancy.re8ch.com/managed-by=tenant-secret-broker,tenancy.re8ch.com/tenant=" + id.Tenant, "-o", "go-template=" + template}
	if input.Name != "" {
		if !validKubernetesName(input.Name) {
			return nil, errors.New("invalid Kubernetes Secret name")
		}
		argv = append(argv, "--field-selector", "metadata.name="+input.Name)
	}
	result := commandResult(ctx, argv, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	items := []any{}
	for _, line := range strings.Split(strings.TrimSpace(asString(result, "stdout")), "\n") {
		if line == "" {
			continue
		}
		fields := strings.SplitN(line, "\t", 4)
		if len(fields) != 4 {
			return nil, errors.New("invalid Kubernetes Secret metadata response")
		}
		keys := strings.Split(strings.TrimSuffix(fields[3], ","), ",")
		if len(keys) == 1 && keys[0] == "" {
			keys = []string{}
		}
		items = append(items, map[string]any{"name": fields[0], "namespace": namespace, "keys": keys, "resourceVersion": fields[1], "createdAt": fields[2]})
	}
	return map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "items": items}, nil
}

func (s *server) deleteSecret(ctx context.Context, id identity, input secretDeleteInput) (any, error) {
	if err := requireOperator(id); err != nil {
		return nil, err
	}
	namespace, err := tenantNamespace(ctx, id, input.Namespace)
	if err != nil {
		return nil, err
	}
	if !validKubernetesName(input.Name) {
		return nil, errors.New("invalid Kubernetes Secret name")
	}
	metadata, err := s.secretMetadata(ctx, id, secretMetadataInput{Namespace: namespace, Name: input.Name})
	if err != nil {
		return nil, err
	}
	if len(slice(object(metadata)["items"])) != 1 {
		return nil, errors.New("Secret is not owned by the tenant Secret broker")
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "name": input.Name, "deleted": false}
	if !input.Apply {
		return preview, nil
	}
	result := commandResult(ctx, []string{"kubectl", "--namespace", namespace, "delete", "secret", input.Name, "-o", "name"}, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	preview["deleted"], preview["operationId"] = true, result["operation_id"]
	return preview, nil
}
