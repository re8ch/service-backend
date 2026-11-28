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
	server       *mcp.Server
	identity     identity
	dynamic      []string
	projectionMu sync.Mutex
	requests     int
}
type activeMCPServers struct {
	mu      sync.Mutex
	entries map[string]*activeMCPServer
	names   func(identity) []string
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
			entry = &activeMCPServer{server: factory(r), identity: id}
			if a.names != nil {
				entry.dynamic = a.names(id)
			}
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

func (a *activeMCPServers) refreshCatalog(s *server, tenant string) bool {
	a.mu.Lock()
	var entries []*activeMCPServer
	for _, entry := range a.entries {
		if tenant == "*" || entry.identity.Tenant == tenant {
			entries = append(entries, entry)
		}
	}
	a.mu.Unlock()
	for _, entry := range entries {
		entry.projectionMu.Lock()
		next, err := s.registerCatalogProjection(entry.server, entry.identity)
		if err != nil {
			entry.projectionMu.Unlock()
			return false
		}
		retained := map[string]bool{}
		for _, name := range next {
			retained[name] = true
		}
		var removed []string
		for _, name := range entry.dynamic {
			if !retained[name] {
				removed = append(removed, name)
			}
		}
		if len(removed) > 0 {
			entry.server.RemoveTools(removed...)
		}
		entry.dynamic = next
		entry.projectionMu.Unlock()
	}
	return true
}
