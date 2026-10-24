package main

import (
	"context"
	"strings"
)

// toolAliases contains compatibility-only names accepted by tools/call. Alias
// names are never registered with the MCP server, so tools/list exposes only
// the canonical surface. Keep aliases for a measured migration window and
// remove them only after their usage metric reaches zero.
type toolAlias struct {
	Canonical      string
	FixedArguments map[string]any
}

var toolAliases = map[string]toolAlias{
	"grafana_capability_list":    {Canonical: "monitoring_catalog", FixedArguments: map[string]any{"action": "list_capabilities"}},
	"grafana_capability_get":     {Canonical: "monitoring_catalog", FixedArguments: map[string]any{"action": "get_capability"}},
	"grafana_datasource_list":    {Canonical: "monitoring_catalog", FixedArguments: map[string]any{"action": "list_datasources"}},
	"grafana_datasource_get":     {Canonical: "monitoring_catalog", FixedArguments: map[string]any{"action": "get_datasource"}},
	"grafana_query":              {Canonical: "monitoring_query"},
	"grafana_analyze":            {Canonical: "monitoring_analyze", FixedArguments: map[string]any{"action": "pipeline"}},
	"grafana_indicators":         {Canonical: "monitoring_analyze", FixedArguments: map[string]any{"action": "indicators"}},
	"grafana_correlate":          {Canonical: "monitoring_analyze", FixedArguments: map[string]any{"action": "correlate"}},
	"grafana_render":             {Canonical: "monitoring_dashboard", FixedArguments: map[string]any{"action": "render"}},
	"grafana_exploration_save":   {Canonical: "monitoring_dashboard", FixedArguments: map[string]any{"action": "save"}},
	"grafana_exploration_delete": {Canonical: "monitoring_dashboard", FixedArguments: map[string]any{"action": "delete"}},
}

type toolAliasUse struct {
	Alias     string
	Canonical string
}

type toolAliasContextKey struct{}

func normalizeToolName(name string) (string, bool) {
	original := name
	for _, prefix := range legacyToolPrefixes {
		if strings.HasPrefix(name, prefix) {
			name = strings.TrimPrefix(name, prefix)
			break
		}
	}
	if alias, ok := toolAliases[name]; ok {
		name = alias.Canonical
	}
	return name, name != original
}

func applyToolAliasArguments(aliasName string, arguments map[string]any) {
	alias, ok := toolAliases[aliasName]
	if !ok {
		return
	}
	for key, value := range alias.FixedArguments {
		arguments[key] = value
	}
}

func toolAliasMetricName(name string) string {
	for _, prefix := range legacyToolPrefixes {
		if strings.HasPrefix(name, prefix) {
			return prefix + "*"
		}
	}
	if _, ok := toolAliases[name]; ok {
		return name
	}
	return unknownToolName
}

func withToolAliasUses(ctx context.Context, uses []toolAliasUse) context.Context {
	if len(uses) == 0 {
		return ctx
	}
	return context.WithValue(ctx, toolAliasContextKey{}, uses)
}

func recordToolAliasUses(ctx context.Context) {
	uses, _ := ctx.Value(toolAliasContextKey{}).([]toolAliasUse)
	for _, use := range uses {
		mcpToolTelemetry.observeAlias(use.Alias, use.Canonical)
	}
}
