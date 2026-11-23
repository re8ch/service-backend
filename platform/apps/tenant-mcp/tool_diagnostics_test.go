package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestToolSentryCorrelatesConcurrentFailuresWithoutPayloadsOrReplay(t *testing.T) {
	const count = 16
	const secret = "private-bearer-and-tool-argument-canary"
	var mu sync.Mutex
	var envelopes [][]byte
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		mu.Lock()
		envelopes = append(envelopes, data)
		mu.Unlock()
		w.WriteHeader(200)
	}))
	defer collector.Close()
	client, err := sentry.NewClient(sentry.ClientOptions{
		Dsn:           strings.Replace(collector.URL, "http://", "http://public@", 1) + "/1",
		EnableTracing: true, TracesSampleRate: 1, SampleRate: 1, Release: "fixture-source-sha", Environment: "test",
		BeforeSend: scrubToolSentryEvent, BeforeSendTransaction: func(e *sentry.Event, _ *sentry.EventHint) *sentry.Event { return scrubToolSentryEvent(e, nil) },
	})
	if err != nil {
		t.Fatal(err)
	}
	diagnostics := &toolDiagnostics{client: client}
	var calls atomic.Int32
	authority := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(503)
		_, _ = io.WriteString(w, secret)
	}))
	defer authority.Close()
	// One shared failed result deliberately checks that diagnostics does not
	// mutate cached/shared backend content or cross-contaminate concurrent calls.
	shared := &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: secret}}}
	app := httptest.NewServer(diagnostics.http(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := identity{Subject: "private-subject-canary", Token: secret, Tenant: "admin"}
		ctx := context.WithValue(r.Context(), identityKey{}, id)
		handler := diagnostics.middleware(func(ctx context.Context, _ string, _ mcp.Request) (mcp.Result, error) {
			_, _ = identityRPC(ctx, id, authority.URL+"/rpc/my_tenant_runtime_contexts", map[string]string{"canary": secret})
			return shared, nil
		})
		result, err := handler(ctx, "tools/call", &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{Name: "tenant_organization_context_list", Arguments: json.RawMessage(`{"canary":"` + secret + `"}`)}})
		if err != nil {
			t.Error(err)
			return
		}
		_ = json.NewEncoder(w).Encode(result)
	})))
	defer app.Close()
	var wg sync.WaitGroup
	operations := make(chan string, count)
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			req, _ := http.NewRequest("POST", app.URL+"/?canary="+secret, strings.NewReader(secret))
			req.Header.Set("Authorization", "Bearer "+secret)
			req.Header.Set("Cookie", secret)
			if i == 0 {
				req.Header.Set("sentry-trace", "0123456789abcdef0123456789abcdef-0123456789abcdef-0")
				req.Header.Set("baggage", "sentry-user_id="+secret)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Error(err)
				return
			}
			defer resp.Body.Close()
			var result mcp.CallToolResult
			if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
				t.Error(err)
				return
			}
			meta, _ := result.Meta["re8ch.com/diagnostics"].(map[string]any)
			op, _ := meta["operationId"].(string)
			eventID, _ := meta["sentryEventId"].(string)
			if op == "" || eventID == "" || op != resp.Header.Get("X-Re8ch-Operation-Id") || !result.IsError {
				t.Error("missing failure correlation")
				return
			}
			operations <- op
		}()
	}
	wg.Wait()
	close(operations)
	if !client.Flush(5 * time.Second) {
		t.Fatal("Sentry transport did not flush")
	}
	if calls.Load() != count {
		t.Fatalf("authority calls=%d want%d, no replay", calls.Load(), count)
	}
	if shared.Meta != nil || len(shared.Content) != 1 {
		t.Fatal("shared backend result mutated")
	}
	ops := map[string]bool{}
	for op := range operations {
		if ops[op] {
			t.Fatal("operation ID reused")
		}
		ops[op] = true
	}
	if len(ops) != count {
		t.Fatalf("completed %d calls", len(ops))
	}
	mu.Lock()
	defer mu.Unlock()
	errorsSeen, tracesSeen := 0, 0
	continued := false
	for _, payload := range envelopes {
		for _, forbidden := range []string{secret, "private-subject-canary", `"request":`, `"user":`, `"server_name":`} {
			if bytes.Contains(payload, []byte(forbidden)) {
				t.Fatalf("Sentry leaked %q", forbidden)
			}
		}
		for _, line := range bytes.Split(payload, []byte("\n")) {
			var event struct {
				EventID   string                     `json:"event_id"`
				Type      string                     `json:"type"`
				Release   string                     `json:"release"`
				Tags      map[string]string          `json:"tags"`
				Contexts  map[string]json.RawMessage `json:"contexts"`
				Exception []sentry.Exception         `json:"exception"`
				Spans     []struct {
					Description string `json:"description"`
					Status      string `json:"status"`
				} `json:"spans"`
			}
			if json.Unmarshal(line, &event) != nil {
				continue
			}
			if event.EventID == "" {
				continue
			}
			if event.Type == "transaction" {
				tracesSeen++
				var trace struct {
					TraceID      string `json:"trace_id"`
					ParentSpanID string `json:"parent_span_id"`
				}
				_ = json.Unmarshal(event.Contexts["trace"], &trace)
				if trace.TraceID == "0123456789abcdef0123456789abcdef" && trace.ParentSpanID == "0123456789abcdef" {
					continued = true
				}
				found := false
				for _, span := range event.Spans {
					if span.Description == "identity.rpc.my_tenant_runtime_contexts" && span.Status == "internal_error" {
						found = true
					}
				}
				if !found {
					t.Fatal("missing failed identity RPC phase")
				}
			} else if len(event.Exception) > 0 {
				errorsSeen++
				if !ops[event.Tags["operation_id"]] || event.Tags["phase"] != "identity.rpc.my_tenant_runtime_contexts" || event.Tags["tool_name"] != "tenant_organization_context_list" {
					t.Fatal("wrong event correlation or failure phase")
				}
				if event.Release != "fixture-source-sha" || len(event.Exception[0].Stacktrace.Frames) == 0 {
					t.Fatal("missing release or stack trace")
				}
			}
		}
	}
	if errorsSeen != count || tracesSeen != count {
		t.Fatalf("errors=%d traces=%d want%d each", errorsSeen, tracesSeen, count)
	}
	if !continued {
		t.Fatal("incoming Gateway trace did not continue")
	}
}

func TestNativeDiagnosticOutcomeSeesExitCodeWithoutMutatingResult(t *testing.T) {
	for _, tc := range []struct {
		body   string
		failed bool
	}{
		{`{"exitCode":1,"stdout":"private","stderr":"private"}`, true},
		{`{"exitCode":0,"stdout":"private"}`, false},
		{`{"resource":"ordinary tool payload"}`, false},
	} {
		r := &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: tc.body}}}
		err := nativeDiagnosticOutcome(context.Background(), r)
		if (err != nil) != tc.failed {
			t.Fatalf("wrong diagnostic outcome for native status")
		}
		if r.IsError || r.Content[0].(*mcp.TextContent).Text != tc.body {
			t.Fatal("reporting changed native result semantics")
		}
	}
}

func TestToolTraceContextPreservesCurrentCancellationAndVerifiedIdentity(t *testing.T) {
	ctx, cancel := context.WithCancel(context.WithValue(context.Background(), identityKey{}, identity{Subject: "fresh", Tenant: "admin"}))
	// The actual diagnostic parent is deliberately pre-authentication.
	fresh := sentry.SetHubOnContext(context.Background(), sentry.NewHub(nil, sentry.NewScope()))
	merged := toolTraceContext{Context: ctx, values: fresh}
	if merged.Value(identityKey{}).(identity).Subject != "fresh" {
		t.Fatal("identity overwritten")
	}
	cancel()
	if merged.Err() != context.Canceled {
		t.Fatal("caller cancellation lost")
	}
}

func TestToolDiagnosticsDoesNotNeedSentryToReturnOperationID(t *testing.T) {
	d := &toolDiagnostics{}
	handler := d.middleware(func(context.Context, string, mcp.Request) (mcp.Result, error) { return &mcp.CallToolResult{}, nil })
	result, err := handler(context.Background(), "tools/call", &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{Name: "byoc_connection_list"}})
	if err != nil {
		t.Fatal(err)
	}
	meta := result.(*mcp.CallToolResult).Meta["re8ch.com/diagnostics"].(map[string]any)
	if meta["operationId"] == "" {
		t.Fatal("missing local correlation")
	}
}
