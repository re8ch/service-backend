package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type moduleBudgetRoundTripper func(*http.Request) (*http.Response, error)

func (f moduleBudgetRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func TestModuleHTTPPreservesLongCallerBudget(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	client := platformModuleHTTPClient(moduleBudgetRoundTripper(func(req *http.Request) (*http.Response, error) {
		deadline, ok := req.Context().Deadline()
		if !ok || time.Until(deadline) < 9*time.Minute {
			t.Fatalf("transport shortened caller's command budget: deadline=%v", deadline)
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("{}")), Request: req}, nil
	}))
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "http://module.test/mcp", strings.NewReader("{}"))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
}

func TestModuleHTTPPreservesCancellationWithoutReplayingPOST(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	client := platformModuleHTTPClient(moduleBudgetRoundTripper(func(req *http.Request) (*http.Response, error) {
		calls++
		cancel()
		return nil, req.Context().Err()
	}))
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "http://module.test/mcp", strings.NewReader("{}"))
	_, err := client.Do(req)
	if !errors.Is(err, context.Canceled) || calls != 1 {
		t.Fatalf("cancellation lost or POST replayed: calls=%d err=%v", calls, err)
	}
}
