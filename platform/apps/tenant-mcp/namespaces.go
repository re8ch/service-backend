package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"regexp"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var kubernetesName = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$`)

func validKubernetesName(value string) bool {
	return len(value) <= 253 && kubernetesName.MatchString(value)
}

func (s *server) registerNamespaceTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"tenant_namespace_list", "tenant_namespace_create", "tenant_namespace_delete"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(name), Name: name, Description: definition.Description, InputSchema: definition.InputSchema},
			func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
				id, err := identityFrom(ctx)
				if err != nil {
					return nil, nil, err
				}
				value, err := s.callNamespace(ctx, id, name, args)
				return nil, value, err
			})
	}
}

func (s *server) callNamespace(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	if name == "tenant_namespace_list" {
		result, err := controlRequest(ctx, id.Tenant, "/v1/namespaces")
		if err != nil {
			return nil, err
		}
		result["tenantRef"] = id.Tenant
		return result, nil
	}
	if id.Role != "operator" && id.Role != "admin" {
		return nil, errors.New("operator or admin role is required")
	}
	namespace, _ := args["namespace"].(string)
	if !validKubernetesName(namespace) {
		return nil, errors.New("invalid Kubernetes name")
	}
	if name == "tenant_namespace_create" {
		payload := map[string]any{"namespaceName": namespace}
		if ttl, ok := args["ttlSeconds"]; ok {
			payload["ttlSeconds"] = ttl
		}
		return controlRequestBody(ctx, id.Tenant, "/v1/namespaces", payload)
	}
	preview := map[string]any{"tenantRef": id.Tenant, "namespace": namespace, "deleted": false}
	if args["apply"] != true {
		return preview, nil
	}
	operation, err := controlRequestMethod(ctx, id.Tenant, "/v1/namespaces/"+url.PathEscape(namespace), http.MethodDelete, nil)
	if err != nil {
		return nil, err
	}
	preview["deleted"], preview["operation"] = true, operation
	return preview, nil
}
