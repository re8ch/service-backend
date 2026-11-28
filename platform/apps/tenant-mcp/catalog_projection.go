package main

import (
	"context"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func (s *server) catalogProjectionNames(id identity) []string {
	if id.Tenant == "" || s.catalogDB() == nil {
		return nil
	}
	entries, _, err := s.mcpCatalog(context.Background(), id.Tenant)
	if err != nil {
		return nil
	}
	var names []string
	for _, entry := range entries {
		if entry.State != "active" || (strings.HasPrefix(entry.Definition.Class, "integration.mcp.") && !containsString(entry.Definition.AudienceOrgRefs, id.Tenant)) {
			continue
		}
		for _, tool := range entry.Definition.Tools {
			names = append(names, tool.Name)
		}
	}
	return names
}

// registerCatalogProjection republishes only definitions whose endpoint is
// already bound by the reviewed module registry. Catalog selects revisions and
// schemas; it cannot inject an endpoint or credential into the execution plane.
func (s *server) registerCatalogProjection(ms *mcp.Server, id identity) ([]string, error) {
	if id.Tenant == "" || s.catalogDB() == nil {
		return nil, nil
	}
	entries, _, err := s.mcpCatalog(context.Background(), id.Tenant)
	if err != nil {
		return nil, err
	}
	return s.installCatalogProjection(ms, id, entries), nil
}

func (s *server) installCatalogProjection(ms *mcp.Server, id identity, entries []mcpCatalogEntry) []string {
	registry := s.moduleRegistry()
	modules := registry.snapshot()
	var names []string
	for _, entry := range entries {
		definition := entry.Definition
		if entry.State != "active" || (strings.HasPrefix(definition.Class, "integration.mcp.") && !containsString(definition.AudienceOrgRefs, id.Tenant)) {
			continue
		}
		backend := ""
		for name, module := range modules {
			if module.backend.URL == definition.Endpoint {
				backend = name
				break
			}
		}
		if backend == "" {
			continue
		}
		for _, published := range definition.Tools {
			toolName := published.Name
			backendName := backend
			description := published.Description
			if description == "" {
				description = "Catalog-published tool from " + definition.ServiceName + "."
			}
			annotations := published.Annotations
			if annotations == nil {
				destructive, openWorld := true, true
				annotations = &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &openWorld}
			}
			mcp.AddTool(ms, &mcp.Tool{Name: toolName, Description: description, InputSchema: published.InputSchema, Annotations: annotations},
				func(ctx context.Context, _ *mcp.CallToolRequest, arguments map[string]any) (*mcp.CallToolResult, any, error) {
					value, err := registry.call(ctx, backendName, func(session *mcp.ClientSession) (any, error) {
						return session.CallTool(ctx, &mcp.CallToolParams{Name: toolName, Arguments: arguments})
					})
					if err != nil {
						return nil, nil, err
					}
					return value.(*mcp.CallToolResult), nil, nil
				})
			names = append(names, toolName)
		}
	}
	return names
}
