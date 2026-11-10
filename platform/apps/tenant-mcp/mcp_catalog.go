package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"
)

var mcpServiceName = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)
var mcpToolName = regexp.MustCompile(`^[a-z][a-z0-9_]{0,127}$`)

var standardMCPPrefixes = map[string]bool{"postgresql": true, "registry": true, "observability": true}

type mcpCatalogTool struct {
	Name        string         `json:"name"`
	Description string         `json:"description,omitempty"`
	InputSchema map[string]any `json:"inputSchema,omitempty"`
}

type mcpCatalogDefinition struct {
	ServiceName string           `json:"serviceName"`
	Class       string           `json:"class"`
	Endpoint    string           `json:"endpoint"`
	AuthMode    string           `json:"authMode"`
	Health      string           `json:"health"`
	Tools       []mcpCatalogTool `json:"tools"`
	Shared      bool             `json:"shared,omitempty"`
}

type mcpCatalogCommand struct {
	Action       string                `json:"action"`
	TenantRef    string                `json:"tenantRef"`
	OperationKey string                `json:"operationKey"`
	Revision     string                `json:"revision,omitempty"`
	Definition   *mcpCatalogDefinition `json:"definition,omitempty"`
}

type mcpCatalogEntry struct {
	Revision   string               `json:"revision"`
	State      string               `json:"state"`
	Definition mcpCatalogDefinition `json:"definition"`
}

func validateMCPDefinition(tenant string, d mcpCatalogDefinition) error {
	if (tenant != "*" && !tenantName.MatchString(tenant)) || !mcpServiceName.MatchString(d.ServiceName) {
		return errors.New("invalid tenantRef or serviceName")
	}
	allowedClass := map[string]bool{
		"database.postgresql.shared.v1": true, "registry.oci.harbor.v1": true,
		"observability.stack.v1": true, "integration.mcp.managed.v1": true,
		"integration.mcp.byoc.v1": true,
	}
	if !allowedClass[d.Class] {
		return errors.New("unsupported MCP service class")
	}
	u, err := url.Parse(d.Endpoint)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && !(u.Scheme == "http" && strings.HasSuffix(u.Hostname(), ".svc"))) {
		return errors.New("endpoint must be HTTPS or an in-cluster service URL")
	}
	if d.AuthMode != "service-token" && d.AuthMode != "oauth-forward" && d.AuthMode != "binding-reference" {
		return errors.New("unsupported authentication mode")
	}
	if len(d.Tools) == 0 || len(d.Tools) > 256 {
		return errors.New("a provider must publish 1 to 256 tools")
	}
	seen := map[string]bool{}
	custom := strings.HasPrefix(d.Class, "integration.mcp.")
	for _, tool := range d.Tools {
		if !mcpToolName.MatchString(tool.Name) || seen[tool.Name] {
			return fmt.Errorf("invalid or duplicate tool %q", tool.Name)
		}
		seen[tool.Name] = true
		prefix := strings.SplitN(tool.Name, "_", 2)[0]
		if custom {
			if standardMCPPrefixes[prefix] || !strings.HasPrefix(tool.Name, strings.ReplaceAll(d.ServiceName, "-", "_")+"_") {
				return fmt.Errorf("custom tool %q must use its service prefix and cannot override a standard tool", tool.Name)
			}
		} else {
			expected := map[string]string{"database.postgresql.shared.v1": "postgresql_", "registry.oci.harbor.v1": "registry_", "observability.stack.v1": "observability_"}[d.Class]
			if !strings.HasPrefix(tool.Name, expected) {
				return fmt.Errorf("standard tool %q must use prefix %q", tool.Name, expected)
			}
		}
	}
	return nil
}

func catalogRevision(tenant string, d mcpCatalogDefinition, operation string) string {
	b, _ := json.Marshal(d)
	h := sha256.Sum256(append(append([]byte(tenant+"\x00"+operation+"\x00"), b...), byte(time.Now().UnixNano())))
	return "mcp_" + hex.EncodeToString(h[:16])
}

func (s *server) applyMCPCatalog(ctx context.Context, cmd mcpCatalogCommand, actor string) (map[string]any, error) {
	if cmd.OperationKey == "" || len(cmd.OperationKey) > 200 {
		return nil, errors.New("operationKey is required")
	}
	tx, err := s.ledger.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var existing string
	if err = tx.QueryRowContext(ctx, `SELECT revision FROM mcp_catalog_revisions WHERE tenant_ref=? AND operation_key=?`, cmd.TenantRef, cmd.OperationKey).Scan(&existing); err == nil {
		return map[string]any{"revision": existing, "idempotentReplay": true}, nil
	} else if !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	var definition mcpCatalogDefinition
	var previous sql.NullString
	_ = tx.QueryRowContext(ctx, `SELECT revision FROM mcp_catalog_heads WHERE tenant_ref=? AND service_name=?`, cmd.TenantRef, func() string {
		if cmd.Definition != nil {
			return cmd.Definition.ServiceName
		}
		return ""
	}()).Scan(&previous)
	state := "active"
	switch cmd.Action {
	case "plan", "apply":
		if cmd.Definition == nil {
			return nil, errors.New("definition is required")
		}
		definition = *cmd.Definition
		if err = validateMCPDefinition(cmd.TenantRef, definition); err != nil {
			return nil, err
		}
		if cmd.Action == "plan" {
			return map[string]any{"valid": true, "tenantRef": cmd.TenantRef, "serviceName": definition.ServiceName}, nil
		}
	case "pause", "rollback":
		if cmd.Revision == "" {
			return nil, errors.New("revision is required")
		}
		var raw string
		if err = tx.QueryRowContext(ctx, `SELECT definition_json FROM mcp_catalog_revisions WHERE tenant_ref=? AND revision=?`, cmd.TenantRef, cmd.Revision).Scan(&raw); err != nil {
			return nil, errors.New("revision not found")
		}
		if err = json.Unmarshal([]byte(raw), &definition); err != nil {
			return nil, err
		}
		if cmd.Action == "pause" {
			state = "paused"
		}
	default:
		return nil, errors.New("unknown action")
	}
	revision := catalogRevision(cmd.TenantRef, definition, cmd.OperationKey)
	raw, _ := json.Marshal(definition)
	if _, err = tx.ExecContext(ctx, `INSERT INTO mcp_catalog_revisions(revision,tenant_ref,service_name,definition_json,state,previous_revision,operation_key,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, revision, cmd.TenantRef, definition.ServiceName, raw, state, previous, cmd.OperationKey, actor, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return nil, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO mcp_catalog_heads(tenant_ref,service_name,revision) VALUES(?,?,?) ON CONFLICT(tenant_ref,service_name) DO UPDATE SET revision=excluded.revision`, cmd.TenantRef, definition.ServiceName, revision); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"revision": revision, "previousRevision": previous.String, "state": state}, nil
}

func (s *server) mcpCatalog(ctx context.Context, tenant string) ([]mcpCatalogEntry, string, error) {
	rows, err := s.ledger.QueryContext(ctx, `SELECT h.tenant_ref,r.revision,r.state,r.definition_json FROM mcp_catalog_heads h JOIN mcp_catalog_revisions r ON r.revision=h.revision WHERE h.tenant_ref IN ('*',?) ORDER BY r.service_name,h.tenant_ref`, tenant)
	if err != nil {
		return nil, "", err
	}
	defer rows.Close()
	byService := map[string]mcpCatalogEntry{}
	owners := map[string]string{}
	for rows.Next() {
		var e mcpCatalogEntry
		var raw, owner string
		if err = rows.Scan(&owner, &e.Revision, &e.State, &raw); err != nil {
			return nil, "", err
		}
		if err = json.Unmarshal([]byte(raw), &e.Definition); err != nil {
			return nil, "", err
		}
		if current, exists := owners[e.Definition.ServiceName]; !exists || (current == "*" && owner == tenant) {
			byService[e.Definition.ServiceName], owners[e.Definition.ServiceName] = e, owner
		}
	}
	var entries []mcpCatalogEntry
	for _, e := range byService {
		entries = append(entries, e)
	}
	sort.SliceStable(entries, func(i, j int) bool { return entries[i].Definition.ServiceName < entries[j].Definition.ServiceName })
	hashes := sha256.New()
	for _, e := range entries {
		hashes.Write([]byte(e.Revision))
	}
	return entries, hex.EncodeToString(hashes.Sum(nil)[:16]), rows.Err()
}

func internalTokenOK(r *http.Request) bool {
	secret := os.Getenv("MCP_CATALOG_INTERNAL_TOKEN")
	given := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	return secret != "" && len(secret) == len(given) && subtle.ConstantTimeCompare([]byte(secret), []byte(given)) == 1
}

func (s *server) mcpCatalogHTTP(w http.ResponseWriter, r *http.Request) {
	tenant := s.catalogTenant(r)
	if !tenantName.MatchString(tenant) {
		writeJSON(w, 401, map[string]string{"error": "verified tenantRef required"})
		return
	}
	entries, revision, err := s.mcpCatalog(r.Context(), tenant)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "catalog unavailable"})
		return
	}
	w.Header().Set("ETag", `"`+revision+`"`)
	w.Header().Set("Cache-Control", "private, max-age=30")
	writeJSON(w, 200, map[string]any{"tenantRef": tenant, "revision": revision, "backends": entries})
}

func (s *server) catalogTenant(r *http.Request) string {
	if internalTokenOK(r) {
		return r.Header.Get("X-RE8CH-Tenant-Ref")
	}
	if bearer := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "); bearer != "" {
		if id, err := s.authenticate(r.Context(), bearer, r.Header.Get("X-RE8CH-Tenant")); err == nil {
			return id.Tenant
		}
	}
	return ""
}

func (s *server) mcpCatalogEventsHTTP(w http.ResponseWriter, r *http.Request) {
	tenant := s.catalogTenant(r)
	if !tenantName.MatchString(tenant) {
		writeJSON(w, 401, map[string]string{"error": "verified tenantRef required"})
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, 500, map[string]string{"error": "streaming unavailable"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	last := ""
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		_, revision, err := s.mcpCatalog(r.Context(), tenant)
		if err != nil {
			return
		}
		if revision != last {
			fmt.Fprintf(w, "event: revision\ndata: %s\n\n", revision)
			flusher.Flush()
			last = revision
		}
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) mcpCatalogAdminHTTP(w http.ResponseWriter, r *http.Request) {
	secret := os.Getenv("DYNAMIC_SKILL_ADMIN_TOKEN")
	given := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if secret == "" || len(secret) != len(given) || subtle.ConstantTimeCompare([]byte(secret), []byte(given)) != 1 {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	var cmd mcpCatalogCommand
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&cmd); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	actor := strings.TrimSpace(r.Header.Get("X-RE8CH-Admin-Subject"))
	if actor == "" {
		writeJSON(w, 400, map[string]string{"error": "admin subject required"})
		return
	}
	result, err := s.applyMCPCatalog(r.Context(), cmd, actor)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, result)
}
