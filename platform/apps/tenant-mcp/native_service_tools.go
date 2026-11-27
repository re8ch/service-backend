package main

import (
	"context"
	"errors"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type nativeServiceMCP struct {
	Backend string
	Prefix  string
}

var nativeServiceMCPs = []nativeServiceMCP{
	{Backend: "pg_re8ch", Prefix: "pg_re8ch"},
	{Backend: "pg_billing", Prefix: "pg_billing"},
	{Backend: "pg_identity", Prefix: "pg_identity"},
	{Backend: "grafana", Prefix: "grafana"},
}

var readOnlySQL = regexp.MustCompile(`(?is)^\s*(select|with|show|explain|values)\b`)
var mutatingSQL = regexp.MustCompile(`(?is)\b(insert|update|delete|merge|alter|drop|create|truncate|grant|revoke|copy|call|do|vacuum|reindex|cluster|refresh|comment|set)\b`)

func nativeServiceToolAllowed(backend, name string) bool {
	lower := strings.ToLower(name)
	for _, marker := range []string{"create", "delete", "update", "apply", "deploy", "migration", "reset", "write", "secret", "token", "service_role"} {
		if strings.Contains(lower, marker) {
			return false
		}
	}
	return backend == "grafana" || lower == "execute_sql" || lower == "query" || lower == "query_logs" ||
		strings.HasPrefix(lower, "list_") || strings.HasPrefix(lower, "get_") || strings.HasPrefix(lower, "search_") || strings.HasPrefix(lower, "generate_")
}

func validateNativeServiceCall(backend, name string, args map[string]any) error {
	if !nativeServiceToolAllowed(backend, name) {
		return errors.New("native service tool is not published through the read-only platform surface")
	}
	if backend != "grafana" && strings.ToLower(name) == "execute_sql" {
		query := asString(args, "query")
		if query == "" {
			query = asString(args, "sql")
		}
		trimmed := strings.TrimSpace(query)
		if !readOnlySQL.MatchString(trimmed) || mutatingSQL.MatchString(trimmed) || strings.Contains(strings.TrimSuffix(trimmed, ";"), ";") {
			return errors.New("Supabase execute_sql accepts one read-only SELECT, WITH, SHOW, EXPLAIN, or VALUES statement")
		}
	}
	return nil
}

// registerNativeServiceInstanceTools republishes the native MCP schemas of
// adopted Admin services. The tenant gateway remains the authentication and
// authorization boundary; the internal Supabase MCP endpoints are never made
// public directly.
func (s *server) registerNativeServiceInstanceTools(ms *mcp.Server) {
	if os.Getenv("NATIVE_SERVICE_MCP_ENABLED") != "true" {
		return
	}
	registry := s.moduleRegistry()
	for _, service := range nativeServiceMCPs {
		entry := registry.snapshot()[service.Backend]
		if entry == nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		tools, err := entry.discover(ctx, service.Backend)
		cancel()
		if err != nil {
			continue
		}
		for _, discovered := range tools {
			upstream := asString(discovered, "name")
			if !nativeServiceToolAllowed(service.Backend, upstream) {
				continue
			}
			name := service.Prefix + "_" + upstream
			description := "Read-only native MCP tool for service://admin/" + strings.ReplaceAll(service.Prefix, "_", "-") + ". " + asString(discovered, "description")
			schema, _ := discovered["inputSchema"].(map[string]any)
			backend := service.Backend
			upstreamName := upstream
			destructive := false
			openWorld := true
			mcp.AddTool(ms, &mcp.Tool{
				Name:        name,
				Description: description,
				InputSchema: schema,
				Annotations: &mcp.ToolAnnotations{
					ReadOnlyHint:    true,
					DestructiveHint: &destructive,
					OpenWorldHint:   &openWorld,
				},
			},
				func(ctx context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
					if _, err := platformIdentity(ctx); err != nil {
						return nil, nil, err
					}
					if err := validateNativeServiceCall(backend, upstreamName, input); err != nil {
						return nil, nil, err
					}
					value, err := registry.call(ctx, backend, func(session *mcp.ClientSession) (any, error) {
						return session.CallTool(ctx, &mcp.CallToolParams{Name: upstreamName, Arguments: input})
					})
					if err != nil {
						return nil, nil, err
					}
					return value.(*mcp.CallToolResult), nil, nil
				})
		}
	}
}
