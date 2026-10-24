package main

import (
	"context"
	"testing"
)

func TestNormalizeToolNameSupportsPrefixesAndHiddenAliases(t *testing.T) {
	previous := toolAliases
	toolAliases = map[string]string{"grafana_query": "monitoring_query"}
	t.Cleanup(func() { toolAliases = previous })

	tests := []struct {
		input, canonical string
		changed          bool
	}{
		{input: "monitoring_query", canonical: "monitoring_query"},
		{input: "grafana_query", canonical: "monitoring_query", changed: true},
		{input: "re8ch_review_demo.grafana_query", canonical: "monitoring_query", changed: true},
		{input: "re8ch.monitoring_query", canonical: "monitoring_query", changed: true},
	}
	for _, test := range tests {
		canonical, changed := normalizeToolName(test.input)
		if canonical != test.canonical || changed != test.changed {
			t.Errorf("normalizeToolName(%q) = %q/%v, want %q/%v", test.input, canonical, changed, test.canonical, test.changed)
		}
	}
	if got := toolAliasMetricName("re8ch_review_demo.grafana_query"); got != "re8ch_review_demo.*" {
		t.Fatalf("prefixed alias metric label = %q", got)
	}
	if got := toolAliasMetricName("grafana_query"); got != "grafana_query" {
		t.Fatalf("explicit alias metric label = %q", got)
	}
}

func TestRecordToolAliasUses(t *testing.T) {
	previous := mcpToolTelemetry
	mcpToolTelemetry = newToolTelemetry()
	t.Cleanup(func() { mcpToolTelemetry = previous })

	ctx := withToolAliasUses(context.Background(), []toolAliasUse{{Alias: "grafana_query", Canonical: "monitoring_query"}})
	recordToolAliasUses(ctx)
	key := toolAliasMetricKey{Alias: "grafana_query", CanonicalName: "monitoring_query"}
	if got := mcpToolTelemetry.aliases[key]; got != 1 {
		t.Fatalf("alias count = %d, want 1", got)
	}
}
