package resultproxy

import (
	"context"
	"crypto/rsa"
	"net/http"
	"strings"
	"sync"
	"time"
)

type KeySource interface {
	Keys(context.Context) (map[string]*rsa.PublicKey, error)
}

// Handler performs authentication before touching the run store. The global
// limiter is deliberately bounded (no attacker-controlled per-IP map).
type Handler struct {
	Policy Policy
	Keys   KeySource
	Store  Store
	mu     sync.Mutex
	next   time.Time
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	deny := func(code int) { http.Error(w, "report unavailable", code) }
	if r.Method != http.MethodPost {
		deny(http.StatusMethodNotAllowed)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/api-conformance/v1/reports/")
	if name == r.URL.Path || !identifier(name) || r.URL.RawQuery != "" || r.ContentLength != 0 || len(r.TransferEncoding) != 0 {
		deny(http.StatusBadRequest)
		return
	}
	h.mu.Lock()
	now := time.Now()
	limited := now.Before(h.next)
	if !limited {
		h.next = now.Add(100 * time.Millisecond)
	}
	h.mu.Unlock()
	if limited {
		w.Header().Set("Retry-After", "1")
		deny(http.StatusTooManyRequests)
		return
	}
	auth := r.Header.Values("Authorization")
	if len(auth) != 1 || !strings.HasPrefix(auth[0], "Bearer ") || len(auth[0]) > 16391 || h.Keys == nil || h.Store == nil {
		deny(http.StatusUnauthorized)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	keys, err := h.Keys.Keys(ctx)
	if err != nil {
		deny(http.StatusServiceUnavailable)
		return
	}
	c, err := Verify(strings.TrimPrefix(auth[0], "Bearer "), keys, h.Policy, time.Now())
	if err != nil {
		deny(http.StatusUnauthorized)
		return
	}
	body, err := Redeem(ctx, h.Store, name, c, time.Now())
	if err != nil {
		deny(http.StatusForbidden)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

// Server bounds slow headers, bodies, writes and keep-alive connections. No
// access logger records Authorization headers or OIDC tokens.
func Server(address string, handler http.Handler) *http.Server {
	return &http.Server{Addr: address, Handler: handler, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 20 * 1024}
}
