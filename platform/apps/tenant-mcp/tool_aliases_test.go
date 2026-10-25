package main

import (
	"context"
	"testing"
)

func TestNormalizeToolNameSupportsPrefixesAndHiddenAliases(t *testing.T) {
	tests := []struct {
		input, canonical string
		changed          bool
	}{
		{input: "monitoring_query", canonical: "monitoring_query"},
		{input: "grafana_query", canonical: "monitoring_query", changed: true},
		{input: "harbor_build_start", canonical: "runtime_image_build", changed: true},
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

func TestRuntimeImageAliasRewriteInjectsCanonicalAction(t *testing.T) {
	request := map[string]any{"method": "tools/call", "params": map[string]any{
		"name":      "harbor_tag_delete",
		"arguments": map[string]any{"action": "create_tag", "repository": "app", "reference": "sha256:abc", "tag": "old"},
	}}
	uses := []toolAliasUse{}
	if !rewriteToolNames(request, &uses) {
		t.Fatal("legacy Harbor call was not rewritten")
	}
	params := object(request["params"])
	if params["name"] != "runtime_image_manage" || object(params["arguments"])["action"] != "delete_tag" {
		t.Fatalf("unexpected rewritten call: %#v", request)
	}
	if len(uses) != 1 || uses[0].Alias != "harbor_tag_delete" || uses[0].Canonical != "runtime_image_manage" {
		t.Fatalf("unexpected alias observation: %#v", uses)
	}
}

func TestMonitoringAliasArgumentsCannotOverrideAction(t *testing.T) {
	arguments := map[string]any{"action": "delete", "capability_id": "query"}
	applyToolAliasArguments("grafana_capability_get", arguments)
	if arguments["action"] != "get_capability" {
		t.Fatalf("compatibility arguments selected %q", arguments["action"])
	}
}

func TestMonitoringAliasRewriteInjectsCanonicalAction(t *testing.T) {
	request := map[string]any{"method": "tools/call", "params": map[string]any{
		"name":      "re8ch_review_demo.grafana_indicators",
		"arguments": map[string]any{"indicators": []any{map[string]any{"type": "slope"}}},
	}}
	uses := []toolAliasUse{}
	if !rewriteToolNames(request, &uses) {
		t.Fatal("legacy monitoring call was not rewritten")
	}
	params := object(request["params"])
	if params["name"] != "monitoring_analyze" || object(params["arguments"])["action"] != "indicators" {
		t.Fatalf("unexpected rewritten call: %#v", request)
	}
	if len(uses) != 1 || uses[0].Alias != "re8ch_review_demo.*" || uses[0].Canonical != "monitoring_analyze" {
		t.Fatalf("unexpected alias observation: %#v", uses)
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
