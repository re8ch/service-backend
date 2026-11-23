package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
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

var toolLatencyBuckets = [...]float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600}

type toolMetricKey struct {
	ToolName      string
	Outcome       string
	ErrorCategory string
}

type toolMetricValue struct {
	Count   uint64
	Sum     float64
	Buckets [len(toolLatencyBuckets) + 1]uint64
}

type httpMetricKey struct{ Phase, Method, StatusClass, Outcome, ErrorCategory string }

type toolAliasMetricKey struct {
	Alias         string
	CanonicalName string
}

// toolTelemetry deliberately records no tenant, subject, session, arguments,
// result content, or free-form error text. Tool names are bounded to prevent an
// unauthenticated or buggy client from creating unbounded metric cardinality.
type toolTelemetry struct {
	mu         sync.Mutex
	toolNames  map[string]struct{}
	values     map[toolMetricKey]*toolMetricValue
	aliases    map[toolAliasMetricKey]uint64
	httpValues map[httpMetricKey]*toolMetricValue
}

func newToolTelemetry() *toolTelemetry {
	return &toolTelemetry{toolNames: make(map[string]struct{}), values: make(map[toolMetricKey]*toolMetricValue), aliases: make(map[toolAliasMetricKey]uint64), httpValues: make(map[httpMetricKey]*toolMetricValue)}
}

func (t *toolTelemetry) boundedToolName(name string) string {
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
	name = t.boundedToolName(name)
	key := toolMetricKey{ToolName: name, Outcome: outcome, ErrorCategory: category}
	value := t.values[key]
	if value == nil {
		value = &toolMetricValue{}
		t.values[key] = value
	}
	observeToolDuration(value, elapsed)
}

func observeToolDuration(value *toolMetricValue, elapsed time.Duration) {
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

func (t *toolTelemetry) observeHTTP(phase, method string, status int, category string, elapsed time.Duration) {
	switch method {
	case "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE":
	default:
		method = "OTHER"
	}
	statusClass := "none"
	if status >= 100 && status < 600 {
		statusClass = strconv.Itoa(status/100) + "xx"
	}
	outcome := "success"
	if category != "none" {
		outcome = "error"
	}
	key := httpMetricKey{phase, method, statusClass, outcome, category}
	t.mu.Lock()
	defer t.mu.Unlock()
	value := t.httpValues[key]
	if value == nil {
		value = &toolMetricValue{}
		t.httpValues[key] = value
	}
	observeToolDuration(value, elapsed)
}

func observedToolOutcome(ctx context.Context, result mcp.Result, err error) (string, string) {
	if state := diagnosticState(ctx); state != nil {
		state.failureMu.Lock()
		defer state.failureMu.Unlock()
		if state.failureCaptured {
			return "error", state.errorCategory
		}
	}
	return toolCallOutcome(result, err)
}

func (t *toolTelemetry) observeAlias(alias, canonical string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	canonical = t.boundedToolName(canonical)
	t.aliases[toolAliasMetricKey{Alias: alias, CanonicalName: canonical}]++
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
		outcome, category := observedToolOutcome(ctx, result, err)
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
	aliasKeys := make([]toolAliasMetricKey, 0, len(t.aliases))
	aliases := make(map[toolAliasMetricKey]uint64, len(t.aliases))
	for key, value := range t.aliases {
		aliasKeys = append(aliasKeys, key)
		aliases[key] = value
	}
	httpKeys := make([]httpMetricKey, 0, len(t.httpValues))
	httpValues := make(map[httpMetricKey]toolMetricValue, len(t.httpValues))
	for key, value := range t.httpValues {
		httpKeys = append(httpKeys, key)
		httpValues[key] = *value
	}
	t.mu.Unlock()
	sort.Slice(keys, func(i, j int) bool {
		return strings.Compare(metricLabels(keys[i]), metricLabels(keys[j])) < 0
	})
	sort.Slice(aliasKeys, func(i, j int) bool {
		left := aliasKeys[i].Alias + "\x00" + aliasKeys[i].CanonicalName
		right := aliasKeys[j].Alias + "\x00" + aliasKeys[j].CanonicalName
		return left < right
	})
	sort.Slice(httpKeys, func(i, j int) bool { return httpMetricLabels(httpKeys[i]) < httpMetricLabels(httpKeys[j]) })

	_, _ = io.WriteString(writer, "# HELP re8ch_mcp_tool_calls_total Completed authenticated MCP tool calls.\n")
	_, _ = io.WriteString(writer, "# TYPE re8ch_mcp_tool_calls_total counter\n")
	for _, key := range keys {
		value := values[key]
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_calls_total{%s} %d\n", metricLabels(key), value.Count)
	}
	_, _ = io.WriteString(writer, "# HELP re8ch_mcp_tool_alias_calls_total Authenticated MCP calls made through a compatibility alias.\n")
	_, _ = io.WriteString(writer, "# TYPE re8ch_mcp_tool_alias_calls_total counter\n")
	for _, key := range aliasKeys {
		_, _ = fmt.Fprintf(writer, "re8ch_mcp_tool_alias_calls_total{alias=%s,canonical_tool_name=%s} %d\n", strconv.Quote(key.Alias), strconv.Quote(key.CanonicalName), aliases[key])
	}
	_, _ = io.WriteString(writer, "# HELP re8ch_mcp_tool_call_duration_seconds Authenticated MCP tool call duration.\n")
	_, _ = io.WriteString(writer, "# TYPE re8ch_mcp_tool_call_duration_seconds histogram\n")
	for _, key := range keys {
		value := values[key]
		writeToolHistogram(writer, "re8ch_mcp_tool_call_duration_seconds", metricLabels(key), value)
	}
	_, _ = io.WriteString(writer, "# HELP re8ch_toolchain_http_requests_total Completed tool entrypoint requests including pre-tool authentication failures.\n# TYPE re8ch_toolchain_http_requests_total counter\n")
	_, _ = io.WriteString(writer, "# HELP re8ch_toolchain_http_request_duration_seconds Tool entrypoint duration.\n# TYPE re8ch_toolchain_http_request_duration_seconds histogram\n")
	for _, key := range httpKeys {
		value := httpValues[key]
		labels := httpMetricLabels(key)
		_, _ = fmt.Fprintf(writer, "re8ch_toolchain_http_requests_total{%s} %d\n", labels, value.Count)
		writeToolHistogram(writer, "re8ch_toolchain_http_request_duration_seconds", labels, value)
	}
	_, _ = fmt.Fprintf(writer, "# HELP re8ch_toolchain_build_info Immutable tool source for Sentry/GitHub correlation.\n# TYPE re8ch_toolchain_build_info gauge\nre8ch_toolchain_build_info{component=\"tenant-mcp\",release=%s,repository=\"re8ch/service-backend\",environment=%s} 1\n", strconv.Quote(diagnosticMetricRelease()), strconv.Quote(diagnosticMetricEnvironment()))
}

func httpMetricLabels(key httpMetricKey) string {
	return fmt.Sprintf("component=\"tenant-mcp\",environment=%s,phase=%s,method=%s,status_class=%s,outcome=%s,error_category=%s", strconv.Quote(diagnosticMetricEnvironment()), strconv.Quote(key.Phase), strconv.Quote(key.Method), strconv.Quote(key.StatusClass), strconv.Quote(key.Outcome), strconv.Quote(key.ErrorCategory))
}

var metricReleasePattern = regexp.MustCompile(`^[a-z0-9._-]+@[a-f0-9]{40}$`)

func diagnosticMetricRelease() string {
	value := os.Getenv("SENTRY_RELEASE")
	if !metricReleasePattern.MatchString(value) {
		return "unknown"
	}
	return value
}
func diagnosticMetricEnvironment() string {
	switch value := os.Getenv("SENTRY_ENVIRONMENT"); value {
	case "production", "staging", "development", "test":
		return value
	default:
		return "unknown"
	}
}

func writeToolHistogram(writer io.Writer, prefix, labels string, value toolMetricValue) {
	cumulative := uint64(0)
	for index, upper := range toolLatencyBuckets {
		cumulative += value.Buckets[index]
		_, _ = fmt.Fprintf(writer, "%s_bucket{%s,le=%s} %d\n", prefix, labels, strconv.Quote(strconv.FormatFloat(upper, 'g', -1, 64)), cumulative)
	}
	cumulative += value.Buckets[len(toolLatencyBuckets)]
	_, _ = fmt.Fprintf(writer, "%s_bucket{%s,le=\"+Inf\"} %d\n%s_sum{%s} %g\n%s_count{%s} %d\n", prefix, labels, cumulative, prefix, labels, value.Sum, prefix, labels, value.Count)
}
