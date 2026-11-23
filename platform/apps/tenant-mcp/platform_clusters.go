package main

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var clusterBindingHash = regexp.MustCompile(`^[0-9a-f]{64}$`)

func platformNativeObject(result *mcp.CallToolResult) (map[string]any, error) {
	if result.IsError {
		return nil, errors.New("reviewed cluster binding is unavailable")
	}
	var value map[string]any
	for _, content := range result.Content {
		if text, ok := content.(*mcp.TextContent); ok && json.Unmarshal([]byte(text.Text), &value) == nil && value != nil {
			return value, nil
		}
	}
	if result.StructuredContent != nil {
		body, _ := json.Marshal(result.StructuredContent)
		if json.Unmarshal(body, &value) == nil && value != nil {
			return value, nil
		}
	}
	return nil, errors.New("platform backend returned an invalid cluster object")
}

func platformClusterArguments(ctx context.Context, s *server, ref string, arguments map[string]any) (map[string]any, error) {
	if ref == "" {
		ref = "re8ch"
	}
	if arguments == nil {
		arguments = map[string]any{}
	}
	result, err := callNativePlatformTool(ctx, s, "clusters.resolve", map[string]any{"clusterRef": ref})
	if err != nil {
		return nil, err
	}
	binding, err := platformNativeObject(result)
	if err != nil {
		return nil, err
	}
	digest, _ := binding["bindingDigest"].(string)
	if binding["clusterRef"] != ref || !clusterBindingHash.MatchString(digest) {
		return nil, errors.New("platform backend returned an invalid cluster binding")
	}
	arguments["clusterRef"], arguments["clusterBindingDigest"] = ref, digest
	return arguments, nil
}

// platformKubectlSelection keeps existing argv-only clients target-aware using
// kubectl's standard context selector. Only the reviewed binding resolver may
// translate a context to a transport; it never reaches the backend kubeconfig.
func platformKubectlSelection(input platformKubectlInput) (string, []string, error) {
	ref, selected := input.ClusterRef, false
	argv := make([]string, 0, len(input.Argv))
	for i := 0; i < len(input.Argv); i++ {
		arg := input.Argv[i]
		if arg == "--" {
			argv = append(argv, input.Argv[i:]...)
			break
		}
		contextRef := ""
		switch {
		case arg == "--context":
			i++
			if i >= len(input.Argv) || input.Argv[i] == "" || strings.HasPrefix(input.Argv[i], "-") {
				return "", nil, errors.New("kubectl context requires a reviewed clusterRef")
			}
			contextRef = input.Argv[i]
		case strings.HasPrefix(arg, "--context="):
			contextRef = strings.TrimPrefix(arg, "--context=")
			if contextRef == "" {
				return "", nil, errors.New("kubectl context requires a reviewed clusterRef")
			}
		default:
			argv = append(argv, arg)
			continue
		}
		if selected {
			return "", nil, errors.New("kubectl context must be specified only once")
		}
		if input.ClusterRef != "" && input.ClusterRef != contextRef {
			return "", nil, errors.New("kubectl context conflicts with clusterRef")
		}
		ref, selected = contextRef, true
	}
	if len(argv) == 0 {
		return "", nil, errors.New("kubectl argv is required")
	}
	return ref, argv, nil
}

func (s *server) platformBYOCConnections(ctx context.Context, value any) (any, error) {
	if _, err := platformIdentity(ctx); err != nil {
		return nil, err
	}
	result, err := callNativePlatformTool(ctx, s, "clusters.list", map[string]any{})
	if err != nil {
		return nil, err
	}
	directory, err := platformNativeObject(result)
	if err != nil {
		return nil, err
	}
	view := value.(map[string]any)
	connections := view["connections"].([]map[string]any)
	seen := map[string]bool{}
	for _, entry := range connections {
		if ref, ok := entry["cluster_ref"].(string); ok {
			seen[ref] = true
		}
	}
	for _, raw := range slice(directory["clusters"]) {
		cluster := object(raw)
		ref := asString(cluster, "clusterRef")
		if ref == "" || seen[ref] {
			continue
		}
		seen[ref] = true
		identityRef := asString(cluster, "serviceIdentity")
		connections = append(connections, map[string]any{"connection_ref": "managed-cluster://admin/" + ref,
			"tenant_ref": "admin", "cluster_ref": ref, "identity_ref": identityRef, "state": cluster["state"],
			"source": "managed-cluster-binding", "managed": true, "registration_ref": nil,
			"adapter": cluster["adapter"], "binding_digest": cluster["bindingDigest"],
			"discovery_tool": "byoc_connection_list", "revocation": "reviewed-gitops",
			"kubectl_context": ref, "kubectl": map[string]any{"tool": "platform_kubectl_exec",
				"arguments": map[string]any{"argv": []string{"--context=" + ref, "get", "--raw=/version"}, "timeout": 30}}})
	}
	view["connections"], view["managedTargetsIncluded"] = connections, true
	return view, nil
}

func (s *server) registerPlatformClusterTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_cluster_bindings_list"), Name: "platform_cluster_bindings_list", Description: "List callable Re8ch Admin cluster bindings, including the existing self/re8ch local adapter and reviewed managed BYOC broker targets. Registration policy is separate; no credentials are returned."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			result, err := callNativePlatformTool(ctx, s, "clusters.list", map[string]any{})
			return result, nil, err
		})
}
