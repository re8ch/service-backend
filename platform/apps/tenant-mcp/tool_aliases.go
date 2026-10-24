package main

import (
	"context"
	"strings"
)

// toolAliases contains compatibility-only names accepted by tools/call. Alias
// names are never registered with the MCP server, so tools/list exposes only
// the canonical surface. Keep aliases for a measured migration window and
// remove them only after their usage metric reaches zero.
var toolAliases = map[string]string{}

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
	if canonical, ok := toolAliases[name]; ok {
		name = canonical
	}
	return name, name != original
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
