package main

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var clusterBindingHash = regexp.MustCompile(`^[0-9a-f]{64}$`)

func platformClusterArguments(ctx context.Context, s *server, ref string, arguments map[string]any) (map[string]any, error) {
	if ref == "" {
		return arguments, nil
	}
	result, err := callNativePlatformTool(ctx, s, "clusters.resolve", map[string]any{"clusterRef": ref})
	if err != nil {
		return nil, err
	}
	if result.IsError {
		return nil, errors.New("reviewed cluster binding is unavailable")
	}
	var binding map[string]any
	for _, content := range result.Content {
		if text, ok := content.(*mcp.TextContent); ok && json.Unmarshal([]byte(text.Text), &binding) == nil {
			break
		}
	}
	digest, _ := binding["bindingDigest"].(string)
	if binding["clusterRef"] != ref || !clusterBindingHash.MatchString(digest) {
		return nil, errors.New("platform backend returned an invalid cluster binding")
	}
	arguments["clusterRef"], arguments["clusterBindingDigest"] = ref, digest
	return arguments, nil
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
