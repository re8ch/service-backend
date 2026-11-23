package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/google/uuid"
	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Each HTTP request owns a hub. Never put bearer tokens, arguments, result
// contents, arbitrary URLs or organization/user identifiers in a Sentry scope.
type toolDiagnostics struct{ client *sentry.Client }
type toolDiagnosticKey struct{}
type diagnosticToolNameKey struct{}
type toolRequestDiagnostic struct {
	operationID     string
	ctx             context.Context
	failureMu       sync.Mutex
	failureCaptured bool
	eventID         string
	errorCategory   string
	failurePhase    string
}

// SDK session contexts can outlive HTTP requests. Borrow the fresh request's
// tracing values while preserving the SDK call's cancellation and verified
// identity values (the diagnostic HTTP parent is created before authentication).
type toolTraceContext struct {
	context.Context
	values context.Context
}

func (c toolTraceContext) Value(key any) any {
	if value := c.values.Value(key); value != nil {
		return value
	}
	return c.Context.Value(key)
}

var localToolDiagnostics = &toolDiagnostics{}
var errDiagnosticCommandExit = errors.New("native command returned a nonzero exit code")

func (s *server) diagnostics() *toolDiagnostics {
	if s.toolDiagnostics != nil {
		return s.toolDiagnostics
	}
	return localToolDiagnostics
}

func configuredToolDiagnostics() (*toolDiagnostics, error) {
	if os.Getenv("SENTRY_DSN") == "" {
		return &toolDiagnostics{}, nil
	}
	client, err := sentry.NewClient(sentry.ClientOptions{
		Dsn: os.Getenv("SENTRY_DSN"), Environment: os.Getenv("SENTRY_ENVIRONMENT"),
		Release: os.Getenv("SENTRY_RELEASE"), SampleRate: 1, EnableTracing: true, TracesSampleRate: 1,
		SendDefaultPII: false, AttachStacktrace: true,
		Tags:                  map[string]string{"component": "tenant-mcp"},
		BeforeSend:            scrubToolSentryEvent,
		BeforeSendTransaction: func(e *sentry.Event, _ *sentry.EventHint) *sentry.Event { return scrubToolSentryEvent(e, nil) },
	})
	if err != nil {
		return nil, errors.New("invalid Sentry configuration")
	}
	return &toolDiagnostics{client: client}, nil
}

func scrubToolSentryEvent(e *sentry.Event, _ *sentry.EventHint) *sentry.Event {
	e.Request, e.Attachments, e.Breadcrumbs = nil, nil, nil
	e.User, e.ServerName = sentry.User{}, ""
	e.Modules = nil
	for k := range e.Contexts {
		if k != "trace" {
			delete(e.Contexts, k)
		}
	}
	for i := range e.Exception {
		e.Exception[i].Value = "tool operation failed; see phase and error_category"
		if st := e.Exception[i].Stacktrace; st != nil {
			for j := range st.Frames {
				st.Frames[j].Vars = nil
			}
		}
	}
	for _, thread := range e.Threads {
		if st := thread.Stacktrace; st != nil {
			for i := range st.Frames {
				st.Frames[i].Vars = nil
			}
		}
	}
	return e
}

func diagnosticState(ctx context.Context) *toolRequestDiagnostic {
	v, _ := ctx.Value(toolDiagnosticKey{}).(*toolRequestDiagnostic)
	return v
}

// The SDK may restore an initialized session's context. Restore only this
// request's diagnostic parent, just as its freshly verified identity is restored.
func restoreToolDiagnostic(ctx context.Context, request mcp.Request) context.Context {
	if extra := request.GetExtra(); extra != nil && extra.TokenInfo != nil {
		if state, ok := extra.TokenInfo.Extra["re8ch.tool-diagnostics"].(*toolRequestDiagnostic); ok && state != nil {
			ctx = context.WithValue(ctx, toolDiagnosticKey{}, state)
			ctx = toolTraceContext{Context: ctx, values: state.ctx}
		}
	}
	return ctx
}

func diagnosticCategory(err error) string {
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.Is(err, context.Canceled):
		return "canceled"
	case errors.Is(err, errIdentityUnavailable):
		return "identity_unavailable"
	case errors.Is(err, errInvalidToken):
		return "invalid_token"
	case errors.Is(err, errDiagnosticCommandExit):
		return "command_exit"
	case err != nil:
		return "internal"
	default:
		return "tool"
	}
}

func diagnosticPhase(ctx context.Context, name string) (context.Context, func(error)) {
	if diagnosticState(ctx) == nil {
		return ctx, func(error) {}
	}
	span := sentry.StartSpan(ctx, "re8ch.phase", sentry.WithDescription(name))
	return span.Context(), func(err error) {
		if err != nil {
			span.Status = sentry.SpanStatusInternalError
			span.SetTag("error_category", diagnosticCategory(err))
			state := diagnosticState(ctx)
			tool, _ := ctx.Value(diagnosticToolNameKey{}).(string)
			if tool == "" {
				tool = "mcp_entrypoint"
			}
			captureToolFailure(span.Context(), state.operationID, tool, diagnosticCategory(err), name)
		} else {
			span.Status = sentry.SpanStatusOK
		}
		span.Finish()
	}
}

func diagnosticHTTPStatus(ctx context.Context, code int) {
	if span := sentry.SpanFromContext(ctx); span != nil {
		span.SetTag("http.status_code", strconv.Itoa(code))
	}
}

func captureToolFailure(ctx context.Context, operationID, tool, category, phase string) string {
	state := diagnosticState(ctx)
	if state != nil {
		state.failureMu.Lock()
		defer state.failureMu.Unlock()
		if state.failureCaptured {
			return state.eventID
		}
		state.failureCaptured = true
		state.errorCategory, state.failurePhase = category, phase
	}
	hub := sentry.GetHubFromContext(ctx)
	if hub == nil || hub.Client() == nil {
		return ""
	}
	event := sentry.NewEvent()
	event.Level, event.Message = sentry.LevelError, "RE8CH tool operation failed"
	event.Tags = map[string]string{"operation_id": operationID, "tool_name": tool, "error_category": category, "phase": phase}
	event.Fingerprint = []string{"re8ch-tool", tool, phase, category}
	event.Exception = []sentry.Exception{{Type: "ToolOperationError", Value: category, Stacktrace: sentry.NewStacktrace()}}
	if id := hub.CaptureEvent(event); id != nil {
		if state != nil {
			state.eventID = string(*id)
		}
		return string(*id)
	}
	return ""
}

func logToolDiagnostic(operationID, tool, category, eventID string, elapsed time.Duration) {
	data, _ := json.Marshal(map[string]any{"event": "tool-diagnostic", "operationId": operationID, "tool": tool, "errorCategory": category, "sentryEventId": eventID, "durationMs": elapsed.Milliseconds()})
	log.Print(string(data))
}

func (d *toolDiagnostics) middleware(next mcp.MethodHandler) mcp.MethodHandler {
	return func(ctx context.Context, method string, request mcp.Request) (result mcp.Result, err error) {
		ctx = restoreToolDiagnostic(ctx, request)
		call, ok := request.(*mcp.CallToolRequest)
		if !ok || call.Params == nil {
			return next(ctx, method, request)
		}
		operationID := "op_" + uuid.NewString()
		if parent := diagnosticState(ctx); parent != nil {
			operationID = parent.operationID
		}
		// Use the same bounded label policy as Prometheus; never trust an
		// arbitrary caller-supplied string as a telemetry label.
		mcpToolTelemetry.mu.Lock()
		name := mcpToolTelemetry.boundedToolName(call.Params.Name)
		mcpToolTelemetry.mu.Unlock()
		ctx = context.WithValue(ctx, diagnosticToolNameKey{}, name)
		ctx, finish := diagnosticPhase(ctx, "tool."+name)
		if span := sentry.SpanFromContext(ctx); span != nil {
			id, _ := ctx.Value(identityKey{}).(identity)
			kind := "unassigned"
			if id.Tenant == "admin" {
				kind = "admin"
			} else if id.Tenant != "" {
				kind = "tenant"
			}
			span.SetTag("organization_context", kind)
		}
		started := time.Now()
		defer func() {
			if recovered := recover(); recovered != nil {
				eventID := captureToolFailure(ctx, operationID, name, "panic", "tool."+name)
				logToolDiagnostic(operationID, name, "panic", eventID, time.Since(started))
				finish(errors.New("panic"))
				// Preserve SDK/HTTP panic handling rather than replaying a tool or
				// turning a potentially committed mutation into success.
				panic(recovered)
			}
		}()
		result, err = next(ctx, method, request)
		outcome, category := toolCallOutcome(result, err)
		eventID := ""
		if outcome == "error" {
			category = diagnosticCategory(err)
			eventID = captureToolFailure(ctx, operationID, name, category, "tool."+name)
			finish(errors.New(category))
		} else {
			finish(nil)
		}
		phase := ""
		if state := diagnosticState(ctx); state != nil {
			state.failureMu.Lock()
			if state.failureCaptured {
				eventID = state.eventID
				category, phase = state.errorCategory, state.failurePhase
			}
			state.failureMu.Unlock()
		}
		metadata := map[string]any{"operationId": operationID}
		if phase != "" {
			metadata["phase"], metadata["errorCategory"] = phase, category
		}
		if eventID != "" {
			metadata["sentryEventId"] = eventID
		}
		if span := sentry.SpanFromContext(ctx); span != nil {
			metadata["traceId"] = span.TraceID.String()
		}
		if callResult, ok := result.(*mcp.CallToolResult); ok && callResult != nil {
			copyResult := *callResult
			copyResult.Meta = mcp.Meta{}
			for k, v := range callResult.Meta {
				copyResult.Meta[k] = v
			}
			copyResult.Meta["re8ch.com/diagnostics"] = metadata
			if copyResult.IsError {
				copyResult.Content = append(append([]mcp.Content(nil), callResult.Content...), &mcp.TextContent{Text: "operationId: " + operationID})
			}
			result = &copyResult
		}
		if err != nil {
			code := int64(jsonrpc.CodeInternalError)
			message := "Tool invocation failed"
			var wire *jsonrpc.Error
			if errors.As(err, &wire) {
				code = wire.Code
				message = wire.Message
				if len(wire.Data) > 0 {
					metadata["detail"] = json.RawMessage(wire.Data)
				}
			}
			data, _ := json.Marshal(metadata)
			err = &jsonrpc.Error{Code: code, Message: message + " (operationId: " + operationID + ")", Data: data}
		}
		logToolDiagnostic(operationID, name, category, eventID, time.Since(started))
		return result, err
	}
}

// A successful MCP envelope can still contain a failed native command. Inspect
// only its fixed exitCode field; stdout, stderr and arbitrary payloads never
// become event attributes. Reporting does not change command/result semantics.
func nativeDiagnosticOutcome(ctx context.Context, result *mcp.CallToolResult) error {
	if result == nil {
		return nil
	}
	if result.IsError {
		return errors.New("native tool failed")
	}
	for _, content := range result.Content {
		text, ok := content.(*mcp.TextContent)
		if !ok {
			continue
		}
		var status struct {
			ExitCode *int `json:"exitCode"`
		}
		if json.Unmarshal([]byte(text.Text), &status) == nil && status.ExitCode != nil && *status.ExitCode != 0 {
			if span := sentry.SpanFromContext(ctx); span != nil {
				span.SetTag("native.exit_code", strconv.Itoa(*status.ExitCode))
			}
			return errDiagnosticCommandExit
		}
	}
	return nil
}

type diagnosticResponseWriter struct {
	http.ResponseWriter
	mu          sync.Mutex
	status      int
	writeFailed bool
}

func (w *diagnosticResponseWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *diagnosticResponseWriter) WriteHeader(code int) {
	w.mu.Lock()
	if w.status == 0 {
		w.status = code
	}
	w.mu.Unlock()
	w.ResponseWriter.WriteHeader(code)
}
func (w *diagnosticResponseWriter) Write(data []byte) (int, error) {
	w.mu.Lock()
	if w.status == 0 {
		w.status = 200
	}
	w.mu.Unlock()
	n, err := w.ResponseWriter.Write(data)
	if err != nil {
		w.mu.Lock()
		w.writeFailed = true
		w.mu.Unlock()
	}
	return n, err
}

func (d *toolDiagnostics) http(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		operationID := "op_" + uuid.NewString()
		hub := sentry.NewHub(d.client, sentry.NewScope())
		if gatewayID := r.Header.Get("X-Re8ch-Gateway-Request-Id"); validGatewayDiagnosticID(gatewayID) {
			hub.Scope().SetTag("gateway_request_id", gatewayID)
		}
		ctx := sentry.SetHubOnContext(r.Context(), hub)
		// Only the validated trace identifier crosses the service boundary.
		// Arbitrary incoming baggage must not become Sentry event metadata.
		transaction := sentry.StartTransaction(ctx, "re8ch.mcp.request", sentry.WithOpName("http.server"), sentry.ContinueFromHeaders(r.Header.Get("sentry-trace"), ""), sentry.WithSpanSampled(sentry.SampledTrue))
		transaction.SetTag("operation_id", operationID)
		ctx = transaction.Context()
		ctx = context.WithValue(ctx, toolDiagnosticKey{}, &toolRequestDiagnostic{operationID: operationID, ctx: ctx})
		w.Header().Set("X-Re8ch-Operation-Id", operationID)
		writer := &diagnosticResponseWriter{ResponseWriter: w}
		started := time.Now()
		defer func() {
			recovered := recover()
			writer.mu.Lock()
			status, failed := writer.status, writer.writeFailed
			writer.mu.Unlock()
			transaction.SetTag("http.status_code", strconv.Itoa(status))
			category := "none"
			if recovered != nil {
				category = "panic"
			} else if status >= 500 {
				category = "http_server"
			} else if failed {
				category = "response_delivery"
			}
			if category != "none" {
				transaction.Status = sentry.SpanStatusInternalError
				eventID := captureToolFailure(ctx, operationID, "mcp_entrypoint", category, "mcp.response")
				logToolDiagnostic(operationID, "mcp_entrypoint", category, eventID, time.Since(started))
			} else {
				transaction.Status = sentry.SpanStatusOK
			}
			transaction.Finish()
			if recovered != nil {
				panic(recovered)
			}
		}()
		next.ServeHTTP(writer, r.WithContext(ctx))
	})
}

func validGatewayDiagnosticID(value string) bool {
	if len(value) != 32 {
		return false
	}
	for _, c := range value {
		if !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func diagnosticErrorResponse(ctx context.Context, message string) map[string]string {
	value := map[string]string{"error": message}
	if state := diagnosticState(ctx); state != nil {
		value["operationId"] = state.operationID
	}
	return value
}
