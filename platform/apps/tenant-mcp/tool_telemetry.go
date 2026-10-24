package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	unknownToolName = "__unknown__"
	maxToolNames    = 256
)

var toolLatencyBuckets = [...]float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60}

type toolMetricKey struct {
	ToolName      string
	Outcome       string
	ErrorCategory string
}

type toolMetricValue struct {
	Count   uint64
	Sum     float64
	Buckets [14]uint64
}

// toolTelemetry deliberately records no tenant, subject, session, arguments,
// result content, or free-form error text. Tool names are bounded to prevent an
// unauthenticated or buggy client from creating unbounded metric cardinality.
type toolTelemetry struct {
	mu        sync.Mutex
	toolNames map[string]struct{}
	values    map[toolMetricKey]*toolMetricValue
}

func newToolTelemetry() *toolTelemetry {
	return &toolTelemetry{toolNames: make(map[string]struct{}), values: make(map[toolMetricKey]*toolMetricValue)}
}

func (t *toolTelemetry) canonicalToolName(name string) string {
	if name == "" || len(name) > 128 {
		return unknownToolName
	}
	for _, char := range name {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return unknownToolName
		}
	}
	if _, ok := t.toolNames[name]; ok {
		return name
	}
	if len(t.toolNames) >= maxToolNames {
		return unknownToolName
	}
	t.toolNames[name] = struct{}{}
	return name
}

func (t *toolTelemetry) observe(name, outcome, category string, elapsed time.Duration) {
	t.mu.Lock()
	defer t.mu.Unlock()
	name = t.canonicalToolName(name)
	key := toolMetricKey{ToolName: name, Outcome: outcome, ErrorCategory: category}
	value := t.values[key]
	if value == nil {
		value = &toolMetricValue{}
		t.values[key] = value
	}
	seconds := elapsed.Seconds()
	value.Count++
	value.Sum += seconds
	for index, upper := range toolLatencyBuckets {
		if seconds <= upper {
			value.Buckets[index]++
			return
		}
	}
	value.Buckets[len(toolLatencyBuckets)]++
}

func toolCallOutcome(result mcp.Result, err error) (string, string) {
	if err != nil {
		switch {
		case errors.Is(err, context.DeadlineExceeded):
			return "error", "timeout"
		case errors.Is(err, context.Canceled):
			return "error", "canceled"
		default:
			return "error", "protocol"
		}
	}
	if callResult, ok := result.(*mcp.CallToolResult); ok && callResult.IsError {
		return "error", "tool"
	}
	return "success", "none"
}

func (t *toolTelemetry) middleware(next mcp.MethodHandler) mcp.MethodHandler {
	return func(ctx context.Context, method string, request mcp.Request) (mcp.Result, error) {
		call, ok := request.(*mcp.CallToolRequest)
		if !ok {
			return next(ctx, method, request)
		}
		started := time.Now()
		result, err := next(ctx, method, request)
		outcome, category := toolCallOutcome(result, err)
		t.observe(call.Params.Name, outcome, category, time.Since(started))
		return result, err
	}
}

func metricLabels(key toolMetricKey) string {
	return fmt.Sprintf("tool_name=%s,outcome=%s,error_category=%s", strconv.Quote(key.ToolName), strconv.Quote(key.Outcome), strconv.Quote(key.ErrorCategory))
}

func (t *toolTelemetry) writePrometheus(writer io.Writer) {
	t.mu.Lock()
	keys := make([]toolMetricKey, 0, len(t.values))
	values := make(map[toolMetricKey]toolMetricValue, len(t.values))
	for key, value := range t.values {
		keys = append(keys, key)
		values[key] = *value
	}
	t.mu.Unlock()
	sort.Slice(keys, func(i, j int) bool {
		return strings.Compare(metricLabels(keys[i]), metricLabels(keys[j])) < 0
	})

	_, _ = io.WriteString(writer, "# HELP re8ch_mcp_tool_calls_total Completed authenticated MCP tool calls.\n")
	_, _ = io.WriteString(writer, "# TYPE re8ch_mcp_tool_calls_total counter\n")
	for _, key := range keys {
		value := values[key]
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_calls_total{%s} %d\n", metricLabels(key), value.Count)
	}
	_, _ = io.WriteString(writer, "# HELP re8ch_mcp_tool_call_duration_seconds Authenticated MCP tool call duration.\n")
	_, _ = io.WriteString(writer, "# TYPE re8ch_mcp_tool_call_duration_seconds histogram\n")
	for _, key := range keys {
		value := values[key]
		cumulative := uint64(0)
		for index, upper := range toolLatencyBuckets {
			cumulative += value.Buckets[index]
			_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_call_duration_seconds_bucket{%s,le=%s} %d\n", metricLabels(key), strconv.Quote(strconv.FormatFloat(upper, 'g', -1, 64)), cumulative)
		}
		cumulative += value.Buckets[len(toolLatencyBuckets)]
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_call_duration_seconds_bucket{%s,le=\"+Inf\"} %d\n", metricLabels(key), cumulative)
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_call_duration_seconds_sum{%s} %g\n", metricLabels(key), value.Sum)
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_call_duration_seconds_count{%s} %d\n", metricLabels(key), value.Count)
	}
}
