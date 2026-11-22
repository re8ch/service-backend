package main

import (
	"context"
	"encoding/json"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"sync"
)

type activeMCPServerKey struct{}
type activeMCPServer struct {
	server   *mcp.Server
	requests int
}
type activeMCPServers struct {
	mu      sync.Mutex
	entries map[string]*activeMCPServer
}

// Modern subscriptions span HTTP requests. Share their registry only while a
// request is active; closing the last request releases it, without an idle user
// cache, retained credentials, session TTL or another controller.
func (a *activeMCPServers) handler(next http.Handler, factory func(*http.Request) *mcp.Server) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id, ok := r.Context().Value(identityKey{}).(identity)
		if !ok {
			next.ServeHTTP(w, r)
			return
		}
		id.Token = ""
		raw, _ := json.Marshal(struct {
			Identity identity
			Endpoint string
		}{id, r.URL.Path})
		key := string(raw)
		a.mu.Lock()
		if a.entries == nil {
			a.entries = map[string]*activeMCPServer{}
		}
		entry := a.entries[key]
		if entry == nil {
			entry = &activeMCPServer{server: factory(r)}
			a.entries[key] = entry
		}
		entry.requests++
		a.mu.Unlock()
		defer func() {
			a.mu.Lock()
			entry.requests--
			if entry.requests == 0 {
				delete(a.entries, key)
			}
			a.mu.Unlock()
		}()
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), activeMCPServerKey{}, entry.server)))
	})
}
