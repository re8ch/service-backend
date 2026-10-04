package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func registryFixture(t *testing.T, handler func(context.Context) (any, error)) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	connections := new(atomic.Int32)
	ms := mcp.NewServer(&mcp.Implementation{Name: "fixture", Version: "1"}, nil)
	mcp.AddTool(ms, &mcp.Tool{Name: "tenant_list"}, func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
		v, e := handler(ctx)
		return nil, v, e
	})
	transport := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return ms }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture" {
			w.WriteHeader(401)
			return
		}
		connections.Add(1)
		transport.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv, connections
}
func writeRegistry(t *testing.T, path string, backends map[string]platformBackend) {
	t.Helper()
	data, err := json.Marshal(platformRegistryDocument{Version: 1, Backends: backends})
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path+".tmp", data, 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(path+".tmp", path); err != nil {
		t.Fatal(err)
	}
}
func reloadRegistry(r *platformRegistry) {
	r.mu.Lock()
	r.checked = time.Time{}
	r.mu.Unlock()
	r.reload()
}
func fixtureBackend(endpoint string) platformBackend {
	b := platformBackends["tenant_admin"]
	b.URL = endpoint
	return b
}
func TestModuleDiscoveryIsBoundedCachedAndIndependent(t *testing.T) {
	t.Setenv("SERVICE_API_TOKEN", "fixture")
	healthy, requests := registryFixture(t, func(context.Context) (any, error) { return map[string]bool{"ok": true}, nil })
	stalled := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.Copy(io.Discard, r.Body); <-r.Context().Done() }))
	defer stalled.Close()
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{"healthy": fixtureBackend(healthy.URL), "stalled": fixtureBackend(stalled.URL)})
	registry := newPlatformRegistry(path)
	defer registry.close()
	start := time.Now()
	result := registry.catalog(context.Background())
	if time.Since(start) > 7*time.Second {
		t.Fatal("discovery exceeded aggregate deadline")
	}
	items := result["tools"].([]map[string]any)
	unavailable := result["unavailableBackends"].([]string)
	if len(items) != 1 || items[0]["backend"] != "healthy" || len(unavailable) != 1 || unavailable[0] != "stalled" {
		t.Fatalf("backend isolation failed: %#v", result)
	}
	count := requests.Load()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	registry.catalog(ctx)
	if requests.Load() != count {
		t.Fatal("healthy catalog was not cached")
	}
}
func TestModuleHotReloadDrainsAndKeepsLastValidConfiguration(t *testing.T) {
	t.Setenv("SERVICE_API_TOKEN", "fixture")
	started, finish := make(chan struct{}), make(chan struct{})
	old, _ := registryFixture(t, func(ctx context.Context) (any, error) {
		close(started)
		select {
		case <-finish:
			return map[string]string{"version": "old"}, nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	})
	next, _ := registryFixture(t, func(context.Context) (any, error) { return map[string]string{"version": "new"}, nil })
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{"module": fixtureBackend(old.URL)})
	registry := newPlatformRegistry(path)
	defer registry.close()
	call := func() (any, error) {
		return registry.call(context.Background(), "module", func(s *mcp.ClientSession, _ map[string]bool) (any, error) {
			return s.CallTool(context.Background(), &mcp.CallToolParams{Name: "tenant_list"})
		})
	}
	done := make(chan error, 1)
	go func() { _, err := call(); done <- err }()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("old call did not start")
	}
	writeRegistry(t, path, map[string]platformBackend{"module": fixtureBackend(next.URL)})
	reloadRegistry(registry)
	value, err := call()
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(value)
	if !json.Valid(data) {
		t.Fatal("invalid call result")
	}
	close(finish)
	if err = <-done; err != nil {
		t.Fatalf("hot reload interrupted in-flight call: %v", err)
	}
	if err = os.WriteFile(path, []byte(`{"version":99}`), 0600); err != nil {
		t.Fatal(err)
	}
	reloadRegistry(registry)
	b, ok := registry.backend("module")
	if !ok || b.URL != next.URL {
		t.Fatal("invalid registry replaced last valid configuration")
	}
}
func TestModuleSessionsAreReusedAndWritesAreNotRetried(t *testing.T) {
	t.Setenv("SERVICE_API_TOKEN", "fixture")
	var invocations atomic.Int32
	backend, _ := registryFixture(t, func(context.Context) (any, error) { invocations.Add(1); return map[string]bool{"committed": true}, nil })
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{"module": fixtureBackend(backend.URL)})
	registry := newPlatformRegistry(path)
	defer registry.close()
	var first *mcp.ClientSession
	for i := 0; i < 2; i++ {
		_, err := registry.call(context.Background(), "module", func(s *mcp.ClientSession, _ map[string]bool) (any, error) {
			if first == nil {
				first = s
			} else if s != first {
				t.Error("MCP session was not reused")
			}
			return s.CallTool(context.Background(), &mcp.CallToolParams{Name: "tenant_list"})
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	if invocations.Load() != 2 {
		t.Fatal("write invocation duplicated")
	}
}

func TestCatalogReportsMissingAllowedTools(t *testing.T) {
	t.Setenv("SERVICE_API_TOKEN", "fixture")
	healthy, _ := registryFixture(t, func(context.Context) (any, error) { return map[string]bool{"ok": true}, nil })
	backend := fixtureBackend(healthy.URL)
	backend.Allowed = names("tenant_list absent_z absent_a")
	path := filepath.Join(t.TempDir(), "modules.json")
	writeRegistry(t, path, map[string]platformBackend{"fixture": backend})
	registry := newPlatformRegistry(path)
	defer registry.close()
	result := registry.catalog(context.Background())
	missing := result["missingTools"].([]map[string]any)
	if len(missing) != 2 || missing[0]["name"] != "absent_a" || missing[1]["name"] != "absent_z" || missing[0]["backend"] != "fixture" || missing[0]["status"] != "not_published" {
		t.Fatalf("missing registration evidence: %#v", result)
	}
	items := result["tools"].([]map[string]any)
	if len(items) != 1 || items[0]["name"] != "tenant_list" {
		t.Fatalf("unpublished tools advertised as callable: %#v", result)
	}
	backend.Allowed = names("tenant_list")
	writeRegistry(t, path, map[string]platformBackend{"fixture": backend})
	reloadRegistry(registry)
	result = registry.catalog(context.Background())
	if len(result["missingTools"].([]map[string]any)) != 0 {
		t.Fatalf("stale missing tools after registry reload: %#v", result)
	}
}
