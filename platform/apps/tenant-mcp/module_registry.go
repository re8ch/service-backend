package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const moduleDiscoveryBudget = 5 * time.Second
const moduleCacheLifetime = 30 * time.Second

type platformRegistryDocument struct {
	Version  int                        `json:"version"`
	Backends map[string]platformBackend `json:"backends"`
}

var moduleName = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

func decodePlatformRegistry(data []byte) (platformRegistryDocument, error) {
	var doc platformRegistryDocument
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&doc); err != nil {
		return doc, err
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return doc, errors.New("trailing registry data")
	}
	if doc.Version != 1 || len(doc.Backends) == 0 || len(doc.Backends) > 32 {
		return doc, errors.New("invalid registry version or module count")
	}
	for name, b := range doc.Backends {
		u, err := url.Parse(b.URL)
		if !moduleName.MatchString(name) || err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
			return doc, fmt.Errorf("invalid endpoint for module %q", name)
		}
		if b.Namespace == "" || b.Secret == "" || b.SecretKey == "" || b.Header == "" || strings.ContainsAny(b.Header, "\r\n :") || len(b.Allowed) == 0 {
			return doc, fmt.Errorf("incomplete module %q", name)
		}
		for tool, enabled := range b.Allowed {
			if tool == "" || !enabled {
				return doc, fmt.Errorf("invalid allowlist for module %q", name)
			}
		}
	}
	return doc, nil
}

type platformRegistry struct {
	mu      sync.Mutex
	path    string
	checked time.Time
	digest  [32]byte
	entries map[string]*platformModule
	closed  bool
}
type platformModule struct {
	backend     platformBackend
	mu          sync.Mutex
	session     *mcp.ClientSession
	connecting  chan struct{}
	users       int
	retired     bool
	connectedAt time.Time
	tools       []map[string]any
	cachedAt    time.Time
	cacheError  error
	discovering chan struct{}
}

func newPlatformRegistry(path string) *platformRegistry {
	r := &platformRegistry{path: path, entries: map[string]*platformModule{}}
	for name, b := range platformBackends {
		r.entries[name] = &platformModule{backend: b}
	}
	r.reload()
	return r
}
func (s *server) moduleRegistry() *platformRegistry {
	s.modulesOnce.Do(func() { s.modules = newPlatformRegistry(os.Getenv("PLATFORM_MODULE_REGISTRY")) })
	return s.modules
}
func (r *platformRegistry) reload() {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed || r.path == "" || time.Since(r.checked) < time.Second {
		return
	}
	r.checked = time.Now()
	f, err := os.Open(r.path)
	if err != nil {
		log.Print("platform module registry unavailable; retaining previous configuration")
		return
	}
	data, err := io.ReadAll(io.LimitReader(f, 1<<20+1))
	f.Close()
	if err != nil || len(data) > 1<<20 {
		log.Print("platform module registry read failed; retaining previous configuration")
		return
	}
	digest := sha256.Sum256(data)
	if digest == r.digest {
		return
	}
	doc, err := decodePlatformRegistry(data)
	if err != nil {
		log.Printf("platform module registry rejected: %v; retaining previous configuration", err)
		return
	}
	next := map[string]*platformModule{}
	for name, b := range doc.Backends {
		encoded, _ := json.Marshal(b)
		if old := r.entries[name]; old != nil {
			previous, _ := json.Marshal(old.backend)
			if bytes.Equal(previous, encoded) {
				next[name] = old
				continue
			}
		}
		next[name] = &platformModule{backend: b}
	}
	for name, old := range r.entries {
		if next[name] != old {
			old.retire()
		}
	}
	r.entries = next
	r.digest = digest
	log.Printf("platform module registry loaded: %d modules", len(next))
}
func (r *platformRegistry) snapshot() map[string]*platformModule {
	r.reload()
	r.mu.Lock()
	defer r.mu.Unlock()
	result := map[string]*platformModule{}
	if !r.closed {
		for n, e := range r.entries {
			result[n] = e
		}
	}
	return result
}
func (r *platformRegistry) backend(name string) (platformBackend, bool) {
	e, ok := r.snapshot()[name]
	if !ok {
		return platformBackend{}, false
	}
	return e.backend, true
}
func (r *platformRegistry) close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
	for _, e := range r.entries {
		e.retire()
	}
}
func (e *platformModule) retire() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.retired = true
	if e.users == 0 && e.session != nil {
		session := e.session
		e.session = nil
		go session.Close()
	}
}
func (e *platformModule) release() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.users--
	if e.retired && e.users == 0 && e.session != nil {
		session := e.session
		e.session = nil
		go session.Close()
	}
}
func (e *platformModule) acquire(ctx context.Context, name string) (*mcp.ClientSession, error) {
	for {
		e.mu.Lock()
		if e.retired {
			e.mu.Unlock()
			return nil, errors.New("module configuration changed; refresh catalog")
		}
		// Rotate service credentials on a bounded schedule, after existing calls drain.
		if e.session != nil && e.users == 0 && time.Since(e.connectedAt) > 5*time.Minute {
			old := e.session
			e.session = nil
			go old.Close()
		}
		if e.session != nil {
			e.users++
			session := e.session
			e.mu.Unlock()
			return session, nil
		}
		if wait := e.connecting; wait != nil {
			e.mu.Unlock()
			select {
			case <-wait:
				continue
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		e.connecting = make(chan struct{})
		e.mu.Unlock()
		session, err := connectPlatformModule(ctx, name, e.backend)
		e.mu.Lock()
		close(e.connecting)
		e.connecting = nil
		if err != nil {
			e.mu.Unlock()
			return nil, err
		}
		if e.retired {
			e.mu.Unlock()
			go session.Close()
			return nil, errors.New("module configuration changed; refresh catalog")
		}
		e.session = session
		e.connectedAt = time.Now()
		e.users++
		e.mu.Unlock()
		return session, nil
	}
}
func connectPlatformModule(ctx context.Context, name string, b platformBackend) (*mcp.ClientSession, error) {
	ctx, cancel := context.WithTimeout(ctx, moduleDiscoveryBudget)
	defer cancel()
	token, err := platformCredential(ctx, b)
	if err != nil {
		return nil, err
	}
	endpoint := b.URL
	if override := os.Getenv("PLATFORM_" + strings.ToUpper(name) + "_MCP_URL"); override != "" {
		endpoint = override
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "re8ch-tenant-platform-gateway", Version: "1.1.0"}, nil)
	connecting := new(atomic.Bool)
	connecting.Store(true)
	deadline, _ := ctx.Deadline()
	transport := platformAuthTransport{base: http.DefaultTransport, token: token, header: b.Header, connecting: connecting, connectDeadline: deadline}
	httpClient := &http.Client{Timeout: 60 * time.Second, Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint, HTTPClient: httpClient, DisableStandaloneSSE: true, MaxRetries: -1}, nil)
	connecting.Store(false)
	return session, err
}
func (r *platformRegistry) call(ctx context.Context, name string, fn func(*mcp.ClientSession, map[string]bool) (any, error)) (any, error) {
	e := r.snapshot()[name]
	if e == nil {
		return nil, errors.New("unknown platform backend")
	}
	session, err := e.acquire(ctx, name)
	if err != nil {
		return nil, err
	}
	defer e.release()
	value, err := fn(session, e.backend.Allowed)
	// Never replay an invocation: a failed response may follow a committed write.
	if err != nil {
		e.mu.Lock()
		e.connectedAt = time.Time{}
		e.mu.Unlock()
	}
	return value, err
}
func (e *platformModule) discover(ctx context.Context, name string) (discovered []map[string]any, failure error) {
	for {
		e.mu.Lock()
		if !e.cachedAt.IsZero() && time.Since(e.cachedAt) < moduleCacheLifetime {
			items, err := e.tools, e.cacheError
			e.mu.Unlock()
			return items, err
		}
		if wait := e.discovering; wait != nil {
			e.mu.Unlock()
			select {
			case <-wait:
				continue
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		e.discovering = make(chan struct{})
		e.mu.Unlock()
		break
	}
	defer func() {
		e.mu.Lock()
		e.cachedAt = time.Now()
		e.cacheError = failure
		close(e.discovering)
		e.discovering = nil
		e.mu.Unlock()
	}()
	session, err := e.acquire(ctx, name)
	if err != nil {
		return nil, err
	}
	defer e.release()
	items := []map[string]any{}
	cursor := ""
	for {
		result, err := session.ListTools(ctx, &mcp.ListToolsParams{Cursor: cursor})
		if err != nil {
			return nil, err
		}
		for _, t := range result.Tools {
			if e.backend.Allowed[t.Name] {
				items = append(items, map[string]any{"backend": name, "name": t.Name, "description": t.Description, "inputSchema": t.InputSchema})
			}
		}
		if result.NextCursor == "" {
			break
		}
		cursor = result.NextCursor
	}
	sort.Slice(items, func(i, j int) bool { return items[i]["name"].(string) < items[j]["name"].(string) })
	e.mu.Lock()
	e.tools = items
	e.cachedAt = time.Now()
	e.mu.Unlock()
	return items, nil
}
func (r *platformRegistry) catalog(ctx context.Context) map[string]any {
	entries := r.snapshot()
	names := make([]string, 0, len(entries))
	for name := range entries {
		names = append(names, name)
	}
	sort.Strings(names)
	type discovery struct {
		items []map[string]any
		err   error
	}
	results := make([]discovery, len(names))
	var wg sync.WaitGroup
	for i, name := range names {
		wg.Add(1)
		go func(i int, name string) {
			defer wg.Done()
			deadline, cancel := context.WithTimeout(ctx, moduleDiscoveryBudget)
			defer cancel()
			results[i].items, results[i].err = entries[name].discover(deadline, name)
		}(i, name)
	}
	wg.Wait()
	items := []map[string]any{}
	unavailable := []string{}
	for i, name := range names {
		if results[i].err != nil {
			unavailable = append(unavailable, name)
		} else {
			items = append(items, results[i].items...)
		}
	}
	return map[string]any{"tools": items, "unavailableBackends": unavailable}
}
