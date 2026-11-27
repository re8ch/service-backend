package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func credentialAPIRequest(api *httptest.Server) func(context.Context, string, string, any) (map[string]any, error) {
	return func(ctx context.Context, method, path string, payload any) (map[string]any, error) {
		return kubeRequestHTTP(ctx, api.Client(), api.URL, "synthetic-service-account", method, path, payload)
	}
}

func TestCredentialGETRecoversOnlyTransientResponses(t *testing.T) {
	for _, status := range []int{429, 502, 503, 504} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			var calls atomic.Int32
			api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || r.URL.Path != "/credential" {
					t.Error("unexpected credential operation")
				}
				if calls.Add(1) == 1 {
					w.WriteHeader(status)
					return
				}
				writeJSON(w, 200, map[string]any{"data": map[string]string{"token": "Zml4dHVyZQ=="}})
			}))
			defer api.Close()
			value, err := platformCredentialSecret(context.Background(), "/credential", credentialAPIRequest(api))
			if err != nil || value["data"] == nil || calls.Load() != 2 {
				t.Fatalf("retry failed: calls=%d error=%v", calls.Load(), err)
			}
		})
	}
}

func TestCredentialGETSlowFirstAttemptHasFreshBoundedRetry(t *testing.T) {
	var calls atomic.Int32
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			<-r.Context().Done()
			return
		}
		writeJSON(w, 200, map[string]any{"data": map[string]string{"token": "Zml4dHVyZQ=="}})
	}))
	defer api.Close()
	started := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), moduleConnectBudget)
	defer cancel()
	_, err := platformCredentialSecret(ctx, "/credential", credentialAPIRequest(api))
	if err != nil || calls.Load() != 2 || time.Since(started) > 7*time.Second {
		t.Fatalf("slow GET recovery failed: calls=%d elapsed=%v err=%v", calls.Load(), time.Since(started), err)
	}
}

func TestCredentialGETDoesNotRetryAuthorizationOrMalformedResponse(t *testing.T) {
	for _, status := range []int{200, 401, 403, 404} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			var calls atomic.Int32
			api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				calls.Add(1)
				w.WriteHeader(status)
				w.Write([]byte("not-json"))
			}))
			defer api.Close()
			_, err := platformCredentialSecret(context.Background(), "/credential", credentialAPIRequest(api))
			if err == nil || calls.Load() != 1 {
				t.Fatalf("non-transient read retried: calls=%d err=%v", calls.Load(), err)
			}
		})
	}
}

func TestCredentialGETRetryCountAndCallerCancellationAreBounded(t *testing.T) {
	t.Run("two attempts maximum", func(t *testing.T) {
		var calls atomic.Int32
		api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { calls.Add(1); w.WriteHeader(503) }))
		defer api.Close()
		started := time.Now()
		_, err := platformCredentialSecret(context.Background(), "/credential", credentialAPIRequest(api))
		if err == nil || calls.Load() != 2 || time.Since(started) > time.Second {
			t.Fatalf("unbounded retry: %d %v", calls.Load(), err)
		}
	})
	t.Run("short caller deadline", func(t *testing.T) {
		var calls atomic.Int32
		api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); <-r.Context().Done() }))
		defer api.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Millisecond)
		defer cancel()
		started := time.Now()
		_, err := platformCredentialSecret(ctx, "/credential", credentialAPIRequest(api))
		if !errors.Is(err, context.DeadlineExceeded) || calls.Load() != 1 || time.Since(started) > 500*time.Millisecond {
			t.Fatalf("caller budget lost: %d %v", calls.Load(), err)
		}
	})
	t.Run("cancel backoff", func(t *testing.T) {
		var calls atomic.Int32
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			calls.Add(1)
			w.WriteHeader(503)
			go func() { time.Sleep(10 * time.Millisecond); cancel() }()
		}))
		defer api.Close()
		started := time.Now()
		_, err := platformCredentialSecret(ctx, "/credential", credentialAPIRequest(api))
		if !errors.Is(err, context.Canceled) || calls.Load() != 1 || time.Since(started) > 500*time.Millisecond {
			t.Fatalf("backoff ignored cancellation: %d %v", calls.Load(), err)
		}
	})
}

func TestNoAuthCredentialDoesNotReadAPI(t *testing.T) {
	t.Setenv("KUBERNETES_SERVICE_HOST", "must-not-contact.invalid")
	token, err := platformCredential(context.Background(), platformBackend{AuthMode: "none"})
	if err != nil || token != "" {
		t.Fatalf("no-auth consulted credential API: %q %v", token, err)
	}
}

func TestInteractiveModuleConnectExceedsDiscoveryBudgetWithoutReplayingBusiness(t *testing.T) {
	var initialized atomic.Bool
	var business atomic.Int32
	ms := mcp.NewServer(&mcp.Implementation{Name: "slow-initialize", Version: "1"}, nil)
	mcp.AddTool(ms, &mcp.Tool{Name: "business"}, func(context.Context, *mcp.CallToolRequest, struct{}) (*mcp.CallToolResult, any, error) {
		business.Add(1)
		return nil, map[string]bool{"ok": true}, nil
	})
	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return ms }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if initialized.CompareAndSwap(false, true) {
			select {
			case <-time.After(moduleDiscoveryBudget + 100*time.Millisecond):
			case <-r.Context().Done():
				return
			}
		}
		handler.ServeHTTP(w, r)
	}))
	defer api.Close()
	registry := &platformRegistry{entries: map[string]*platformModule{"module": {backend: platformBackend{URL: api.URL, AuthMode: "none"}}}}
	defer registry.close()
	ctx, cancel := context.WithTimeout(context.Background(), moduleConnectBudget)
	defer cancel()
	var invocations int
	failure := errors.New("response failed after business execution")
	_, err := registry.call(ctx, "module", func(session *mcp.ClientSession) (any, error) {
		invocations++
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "business", Arguments: map[string]any{}})
		if err != nil || result.IsError {
			return nil, errors.New("business did not execute")
		}
		return nil, failure
	})
	if !errors.Is(err, failure) || invocations != 1 || business.Load() != 1 {
		t.Fatalf("business replay or connect-budget regression: invocations=%d calls=%d err=%v", invocations, business.Load(), err)
	}
}

func TestCredentialGETSuccessAndDecoderEOFAreNeverRetried(t *testing.T) {
	for _, body := range []string{`{"data":{"token":"Zml4dHVyZQ=="}}`, "", "{"} {
		t.Run(body, func(t *testing.T) {
			var calls atomic.Int32
			api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { calls.Add(1); w.Write([]byte(body)) }))
			defer api.Close()
			_, err := platformCredentialSecret(context.Background(), "/credential", credentialAPIRequest(api))
			if calls.Load() != 1 || ((err == nil) != (len(body) > 1)) {
				t.Fatalf("decoder/success retry regression: %d %v", calls.Load(), err)
			}
		})
	}
}

func TestModuleAcquireWaitRespectsCallerCancellation(t *testing.T) {
	for _, ready := range []bool{false, true} {
		t.Run(map[bool]string{false: "waiting", true: "ready and cancelled together"}[ready], func(t *testing.T) {
			pending := make(chan struct{})
			module := &platformModule{connecting: pending}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if ready {
				// Both select branches are ready; cancellation must win on the next loop,
				// even if an initializer just published a cached session.
				module.session = &mcp.ClientSession{}
				cancel()
				close(pending)
			} else {
				go func() { time.Sleep(10 * time.Millisecond); cancel() }()
			}
			started := time.Now()
			session, err := module.acquire(ctx, "fixture")
			if session != nil || !errors.Is(err, context.Canceled) || module.users != 0 || time.Since(started) > 500*time.Millisecond {
				t.Fatalf("cancelled acquisition returned or retained session: %v users=%d", err, module.users)
			}
		})
	}
}
