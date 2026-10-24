package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestToolTelemetryPrometheusIsDeidentified(t *testing.T) {
	telemetry := newToolTelemetry()
	telemetry.observe("monitoring_query", "success", "none", 125*time.Millisecond)
	telemetry.observeAlias("grafana_query", "monitoring_query")
	var output strings.Builder
	telemetry.writePrometheus(&output)
	text := output.String()
	for _, expected := range []string{
		`re8ch_mcp_tool_calls_total{tool_name="monitoring_query",outcome="success",error_category="none"} 1`,
		`re8ch_mcp_tool_call_duration_seconds_count{tool_name="monitoring_query",outcome="success",error_category="none"} 1`,
		`re8ch_mcp_tool_alias_calls_total{alias="grafana_query",canonical_tool_name="monitoring_query"} 1`,
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("metrics missing %q:\n%s", expected, text)
		}
	}
	for _, forbidden := range []string{"tenant", "subject", "session", "arguments", "result"} {
		if strings.Contains(text, forbidden+"=") {
			t.Fatalf("metrics contain identifying or content label %q", forbidden)
		}
	}
}

func TestToolTelemetryBoundsToolNameCardinality(t *testing.T) {
	telemetry := newToolTelemetry()
	for index := 0; index < maxToolNames+20; index++ {
		telemetry.observe(fmt.Sprintf("tool_%d", index), "error", "protocol", time.Millisecond)
	}
	telemetry.observe("invalid-tool-name", "error", "protocol", time.Millisecond)
	if len(telemetry.toolNames) > maxToolNames {
		t.Fatalf("tool name cardinality = %d, want <= %d", len(telemetry.toolNames), maxToolNames)
	}
	var output strings.Builder
	telemetry.writePrometheus(&output)
	if !strings.Contains(output.String(), `tool_name="__unknown__"`) {
		t.Fatal("overflow and invalid tool names must use the bounded fallback label")
	}
}

func TestToolCallOutcomeCategories(t *testing.T) {
	tests := []struct {
		name, outcome, category string
		result                  mcp.Result
		err                     error
	}{
		{name: "success", outcome: "success", category: "none", result: &mcp.CallToolResult{}},
		{name: "tool", outcome: "error", category: "tool", result: &mcp.CallToolResult{IsError: true}},
		{name: "protocol", outcome: "error", category: "protocol", err: errors.New("bad request")},
		{name: "timeout", outcome: "error", category: "timeout", err: context.DeadlineExceeded},
		{name: "canceled", outcome: "error", category: "canceled", err: context.Canceled},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			outcome, category := toolCallOutcome(test.result, test.err)
			if outcome != test.outcome || category != test.category {
				t.Fatalf("got %s/%s, want %s/%s", outcome, category, test.outcome, test.category)
			}
		})
	}
}
