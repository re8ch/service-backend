package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/getsentry/sentry-go"
)

type diagnosticRoundTripper func(*http.Request) (*http.Response, error)

func TestDiagnosticWriterTracksFinalStatusAndRealStreamFlush(t *testing.T) {
	statuses := make(chan int, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		wrapped := &diagnosticResponseWriter{ResponseWriter: w}
		wrapped.WriteHeader(http.StatusEarlyHints)
		if err := http.NewResponseController(wrapped).Flush(); err != nil {
			t.Error(err)
		}
		wrapped.WriteHeader(http.StatusServiceUnavailable)
		_, _ = wrapped.Write([]byte("stream"))
		statuses <- wrapped.status
	}))
	defer server.Close()
	response, err := server.Client().Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil || string(body) != "stream" || response.StatusCode != http.StatusOK || <-statuses != http.StatusOK {
		t.Fatalf("stream final status/body mismatch: status=%d body=%q error=%v", response.StatusCode, body, err)
	}
}

func (f diagnosticRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func tracedFixtureContext() context.Context {
	ctx := sentry.SetHubOnContext(context.Background(), sentry.NewHub(nil, sentry.NewScope()))
	span := sentry.StartTransaction(ctx, "fixture", sentry.WithSpanSampled(sentry.SampledTrue))
	ctx = span.Context()
	return context.WithValue(ctx, toolDiagnosticKey{}, &toolRequestDiagnostic{operationID: "op_11111111-1111-4111-8111-111111111111", ctx: ctx})
}

func TestInfrastructureTraceCarrierIsOwnedOriginOnlyAndNeverReplays(t *testing.T) {
	ctx, cancel := context.WithTimeout(tracedFixtureContext(), 10*time.Minute)
	defer cancel()
	var calls atomic.Int32
	failure := errors.New("transport canary")
	base := diagnosticRoundTripper(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		deadline, ok := r.Context().Deadline()
		if !ok || time.Until(deadline) < 9*time.Minute {
			t.Error("caller deadline shortened")
		}
		if r.URL.Host == "owned.invalid" {
			if r.Header.Get("sentry-trace") == "" || r.Header.Get("X-Re8ch-Operation-Id") != diagnosticState(ctx).operationID {
				t.Error("missing native trace carrier")
			}
			if r.Header.Get("Authorization") != "Bearer fixture-auth" {
				t.Error("authentication header changed")
			}
		} else if r.Header.Get("sentry-trace") != "" || r.Header.Get("X-Re8ch-Operation-Id") != "" {
			t.Error("trace carrier sent outside reviewed origin")
		}
		if r.Header.Get("baggage") != "" {
			t.Error("untrusted baggage forwarded")
		}
		return nil, failure
	})
	transport := tracedInfrastructureTransport(base, "https://owned.invalid/mcp", "cloud_platform.http")
	for _, target := range []string{"https://owned.invalid/mcp", "https://other.invalid/private?credential=canary"} {
		req, _ := http.NewRequestWithContext(ctx, "POST", target, strings.NewReader("private tool arguments"))
		req.Header.Set("Authorization", "Bearer fixture-auth")
		req.Header.Set("baggage", "sentry-user_id=canary")
		req.Header.Set("sentry-trace", "untrusted")
		req.Header.Set("X-Re8ch-Operation-Id", "untrusted")
		_, err := transport.RoundTrip(req)
		if !errors.Is(err, failure) {
			t.Fatal("transport error replaced")
		}
		if req.Header.Get("sentry-trace") != "untrusted" {
			t.Fatal("caller request mutated")
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("transport calls=%d; business call replayed", calls.Load())
	}
}

func TestInfrastructureTraceTransportPreservesCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(tracedFixtureContext())
	cancel()
	transport := tracedInfrastructureTransport(diagnosticRoundTripper(func(r *http.Request) (*http.Response, error) {
		if r.Context().Err() != context.Canceled {
			t.Fatal("cancellation lost")
		}
		return nil, r.Context().Err()
	}), "https://owned.invalid", "tenant_control.http")
	req, _ := http.NewRequestWithContext(ctx, "GET", "https://owned.invalid/resolve", nil)
	_, err := transport.RoundTrip(req)
	if err != context.Canceled {
		t.Fatal("cancellation error changed")
	}
}

func TestToolchainMetricsRetainLongCallsAndStableReleaseWithoutTraceLabels(t *testing.T) {
	t.Setenv("SENTRY_RELEASE", "tenant-mcp@0123456789abcdef0123456789abcdef01234567")
	t.Setenv("SENTRY_ENVIRONMENT", "production")
	telemetry := newToolTelemetry()
	telemetry.observe("platform_kubectl_exec", "error", "command_exit", 301*time.Second)
	telemetry.observeHTTP("mcp", "POST", 503, "identity_unavailable", 8*time.Second)
	var b strings.Builder
	telemetry.writePrometheus(&b)
	text := b.String()
	for _, want := range []string{
		`re8ch_mcp_tool_call_duration_seconds_bucket{tool_name="platform_kubectl_exec",outcome="error",error_category="command_exit",le="600"} 1`,
		`re8ch_toolchain_http_requests_total{component="tenant-mcp",environment="production",phase="mcp",method="POST",status_class="5xx",outcome="error",error_category="identity_unavailable"} 1`,
		`re8ch_toolchain_build_info{component="tenant-mcp",release="tenant-mcp@0123456789abcdef0123456789abcdef01234567",repository="re8ch/service-backend",environment="production"} 1`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing correlated metric %s", want)
		}
	}
	for _, forbidden := range []string{"traceId=", "trace_id=", "operationId=", "operation_id=", "sentryEventId=", "token="} {
		if strings.Contains(text, forbidden) {
			t.Fatal("unbounded or sensitive metric label")
		}
	}
}

func TestHTTPDiagnosticsCountsFailuresBeforeToolDispatch(t *testing.T) {
	key := httpMetricKey{"catalog", "GET", "5xx", "error", "http_server"}
	count := func() uint64 {
		mcpToolTelemetry.mu.Lock()
		defer mcpToolTelemetry.mu.Unlock()
		if v := mcpToolTelemetry.httpValues[key]; v != nil {
			return v.Count
		}
		return 0
	}
	before := count()
	response := httptest.NewRecorder()
	(&toolDiagnostics{}).http(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(503)
		_, _ = io.WriteString(w, "private upstream error")
	})).ServeHTTP(response, httptest.NewRequest("GET", "/internal/mcp/catalog", nil))
	if count() != before+1 || response.Header().Get("X-Re8ch-Operation-Id") == "" {
		t.Fatal("entrypoint failure missing metrics/correlation")
	}
}
