package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var legacyToolPrefixes = []string{"re8ch_review_demo.", "re8ch_tenant.", "re8ch."}
var mcpToolTelemetry = newToolTelemetry()

type searchInput struct {
	Query string `json:"query" jsonschema:"Search phrase describing the capability needed"`
}

type loadInput struct {
	SkillRef    string `json:"skillRef" jsonschema:"Published dynamic skill reference returned by dynamic_skill_search"`
	Instruction string `json:"instruction" jsonschema:"The user's current task in natural language"`
}

type submitInput struct {
	Name        string `json:"name" jsonschema:"Stable lowercase skill name"`
	Description string `json:"description" jsonschema:"What this tenant-specific workflow does"`
	Workflow    string `json:"workflow" jsonschema:"Instructions for the current Agent to follow after review"`
	Visibility  string `json:"visibility,omitempty" jsonschema:"Use personal to make the reviewed workflow visible only to this authenticated user; default is tenant"`
}

func (s *server) skillMCP(id identity) *mcp.Server {
	ms := mcp.NewServer(&mcp.Implementation{Name: "re8ch-tenant", Version: "0.3.0"}, &mcp.ServerOptions{Capabilities: &mcp.ServerCapabilities{Tools: &mcp.ToolCapabilities{}, Resources: &mcp.ResourceCapabilities{}}})
	ms.AddReceivingMiddleware(mcpToolTelemetry.middleware)
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_access_status"), Name: "tenant_access_status", Description: "Report this session's runtime class and resource access state."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			caller, ok := ctx.Value(identityKey{}).(identity)
			if !ok || caller.Service || caller.Subject == "" {
				return nil, nil, errors.New("an authenticated user is required")
			}
			status := map[string]any{
				"authenticated":               true,
				"tenantAuthorized":            caller.Tenant != "" || caller.RuntimeTenantRef != "",
				"resourceOperationsAvailable": caller.Tenant != "",
			}
			accessStatus := caller.AccessStatus
			if accessStatus == "" {
				if caller.Tenant != "" {
					accessStatus = "ready"
				} else {
					accessStatus = "no_membership"
				}
			}
			status["status"] = accessStatus
			if caller.RecoveryAction != "" {
				status["recoveryAction"] = caller.RecoveryAction
			}
			if caller.OrganizationRef != "" {
				status["organizationRef"] = caller.OrganizationRef
			}
			if caller.RuntimeTenantRef != "" {
				status["runtimeTenantRef"] = caller.RuntimeTenantRef
			}
			if caller.Tenant == "" {
				if caller.RuntimeTenantRef != "" {
					status["reason"] = "shared runtime class resolved; resource operations are pending owner isolation"
				} else {
					status["reason"] = "no single active runtime tenant resolved"
				}
			}
			return nil, status, nil
		})
	s.registerOrganizationContextTools(ms)
	if !id.Service && id.Subject != "" && id.Tenant == "" {
		return ms
	}
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_self_service_capabilities"), Name: "tenant_self_service_capabilities", Description: "Discover the authenticated active organization's resource operations, Ready namespaces, and current grant entitlements. A grant does not prove provider readiness. Start here before planning Workload, Service, Secret, or managed PublicEdge Exposure; search dynamic_skill_search for reviewed internal workflows."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			caller, err := identityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := selfServiceCapabilities(ctx, caller)
			return nil, value, err
		})
	s.registerClaimTools(ms)
	s.registerNamespaceTools(ms)
	s.registerBYOCTools(ms)
	s.registerServiceTools(ms)
	s.registerHarborTools(ms)
	s.registerGrafanaTools(ms)
	s.registerKubernetesTools(ms)
	s.registerSecretTools(ms)
	s.registerExposureTools(ms)
	if !id.Service && id.Tenant == artcTenant() {
		s.registerArtcTools(ms)
	}
	// Platform administration is a separately verified session capability. It is
	// intentionally independent from the caller's active application tenant so
	// an administrator can operate platform modules while remaining scoped to
	// the Re8ch application organization. Organization/user administration stays
	// restricted to the Admin organization below.
	if id.PlatformAdmin && !id.Service {
		s.registerPlatformTools(ms)
	}
	if id.PlatformAdmin && id.Tenant == "admin" && !id.Service {
		s.registerOrganizationTools(ms)
		s.registerUserTools(ms)
	}
	s.registerResources(ms, id)
	s.registerDynamicSkillTools(ms)
	return ms
}

func (s *server) registerDynamicSkillTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("dynamic_skill_search"), Name: "dynamic_skill_search", Description: "Search reviewed internal and tenant-specific workflows visible to the authenticated active organization. Use tenant_self_service_capabilities first for supported resource operations; use this search to find procedures and integrations not represented by static tools."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input searchInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.search(request{Tenant: id.Tenant, Subject: id.Subject, Query: input.Query})
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("dynamic_skill_load"), Name: "dynamic_skill_load", Description: "Read a published workflow for a natural-language task. The current Agent interprets it using its existing tools and approvals."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input loadInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			value, err := s.load(request{Tenant: id.Tenant, Subject: id.Subject, SkillRef: input.SkillRef, Instruction: input.Instruction})
			return nil, value, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("dynamic_skill_submit"), Name: "dynamic_skill_submit", Description: "Submit a tenant workflow draft for platform review. Drafts cannot be loaded or executed."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input submitInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			if id.Role != "operator" && id.Role != "admin" {
				return nil, nil, errors.New("an operator tenant role is required")
			}
			value, err := s.submit(request{Tenant: id.Tenant, Subject: id.Subject, Name: input.Name, Description: input.Description, Workflow: input.Workflow, Visibility: input.Visibility}, false)
			return nil, value, err
		})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func rewriteReviewDemoToolCall(r *http.Request) error {
	if r.Method != http.MethodPost || r.Body == nil {
		return nil
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return err
	}
	_ = r.Body.Close()
	r.Body = io.NopCloser(bytes.NewReader(body))

	var request any
	if json.Unmarshal(body, &request) != nil {
		return nil
	}
	uses := []toolAliasUse{}
	if !rewriteToolNames(request, &uses) {
		return nil
	}
	normalized, err := json.Marshal(request)
	if err != nil {
		return err
	}
	r.Body = io.NopCloser(bytes.NewReader(normalized))
	r.ContentLength = int64(len(normalized))
	rctx := withToolAliasUses(r.Context(), uses)
	*r = *r.WithContext(rctx)
	return nil
}

func rewriteReviewDemoToolNames(value any) bool {
	return rewriteToolNames(value, nil)
}

func rewriteToolNames(value any, uses *[]toolAliasUse) bool {
	switch request := value.(type) {
	case []any:
		changed := false
		for _, item := range request {
			changed = rewriteToolNames(item, uses) || changed
		}
		return changed
	case map[string]any:
		if request["method"] != "tools/call" {
			return false
		}
		params, ok := request["params"].(map[string]any)
		if !ok {
			return false
		}
		name, ok := params["name"].(string)
		if !ok {
			return false
		}
		unprefixed := name
		for _, prefix := range legacyToolPrefixes {
			if strings.HasPrefix(unprefixed, prefix) {
				unprefixed = strings.TrimPrefix(unprefixed, prefix)
				break
			}
		}
		canonical, changed := normalizeToolName(name)
		if changed {
			params["name"] = canonical
			arguments, _ := params["arguments"].(map[string]any)
			if arguments == nil {
				arguments = map[string]any{}
				params["arguments"] = arguments
			}
			applyToolAliasArguments(unprefixed, arguments)
			if uses != nil {
				*uses = append(*uses, toolAliasUse{Alias: toolAliasMetricName(name), Canonical: canonical})
			}
			return true
		}
		return false
	default:
		return false
	}
}

func (s *server) handler() http.Handler {
	mcpHandler := mcp.NewStreamableHTTPHandler(func(req *http.Request) *mcp.Server {
		id, _ := req.Context().Value(identityKey{}).(identity)
		return s.skillMCP(id)
	},
		&mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true, MaxRequestBodyBytes: 1 << 20})
	mux := http.NewServeMux()
	mux.HandleFunc("GET /.well-known/openai-apps-challenge", openAIChallenge)
	mux.HandleFunc("GET /livez", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok", "profile": "tenant"})
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		mcpToolTelemetry.writePrometheus(w)
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ready, checks := s.readiness(r.Context())
		status := 503
		label := "not-ready"
		if ready {
			status, label = 200, "ready"
		}
		writeJSON(w, status, map[string]any{"status": label, "profile": "tenant", "checks": checks})
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { muxHandlerRedirect(w, r, "/readyz", mux) })
	mux.HandleFunc("GET /install", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		http.Redirect(w, r, "https://tools.re8ch.com/catalog", http.StatusFound)
	})
	metadataHandler := func(w http.ResponseWriter, _ *http.Request) {
		origin := os.Getenv("PUBLIC_MCP_URL")
		if origin == "" {
			origin = "https://tenant.tools.re8ch.com/mcp"
		}
		writeJSON(w, 200, map[string]any{"resource": origin, "authorization_servers": []string{supabaseIssuer}, "bearer_methods_supported": []string{"header"}, "scopes_supported": []string{"openid", "profile", "email", "offline_access"}})
	}
	mux.HandleFunc("GET /.well-known/oauth-protected-resource", metadataHandler)
	mux.HandleFunc("GET /.well-known/oauth-protected-resource/", metadataHandler)
	mux.HandleFunc("POST /oauth/token", s.exchangeToken)
	mux.HandleFunc("POST /oauth/token/compat", oauthTokenCompat)
	mux.HandleFunc("OPTIONS /billing/usage", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Origin") != "https://auth.re8ch.com" {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		billingHeaders(w, r)
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, X-RE8CH-Tenant")
		w.Header().Set("Access-Control-Max-Age", "600")
		w.WriteHeader(204)
	})
	mux.HandleFunc("GET /billing/usage", s.billingHTTP)
	mux.HandleFunc("OPTIONS /self-service/capabilities", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Origin") != "https://auth.re8ch.com" {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		billingHeaders(w, r)
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, X-RE8CH-Tenant")
		w.Header().Set("Access-Control-Max-Age", "600")
		w.WriteHeader(204)
	})
	mux.HandleFunc("GET /self-service/capabilities", s.selfServiceCapabilitiesHTTP)
	mux.HandleFunc("POST /internal/usage-events", s.receiveUsageHTTP)
	mux.HandleFunc("GET /credentials/redeem", func(w http.ResponseWriter, _ *http.Request) { credentialPage(w, redeemPage) })
	mux.HandleFunc("GET /credentials/adopt", func(w http.ResponseWriter, _ *http.Request) { credentialPage(w, adoptPage) })
	mux.HandleFunc("POST /credentials/redeem", s.redeemHTTP)
	mux.HandleFunc("POST /credentials/adopt", s.adoptHTTP)
	mux.HandleFunc("/mcp", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Allow", "POST")
			writeJSON(w, 405, map[string]any{"error": "method_not_allowed", "allowed": []string{"POST"}})
			return
		}
		header := r.Header.Get("Authorization")
		challenge := func() {
			metadata := os.Getenv("PUBLIC_RESOURCE_METADATA_URL")
			if metadata == "" {
				metadata = "https://tenant.tools.re8ch.com/.well-known/oauth-protected-resource/mcp"
			}
			w.Header().Set("WWW-Authenticate", `Bearer realm="`+supabaseIssuer+`", resource_metadata="`+metadata+`"`)
		}
		if !strings.HasPrefix(header, "Bearer ") {
			challenge()
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid_token"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		if err := rewriteReviewDemoToolCall(r); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_request"})
			return
		}
		id, err := s.authenticate(r.Context(), strings.TrimPrefix(header, "Bearer "), r.Header.Get("X-RE8CH-Tenant"))
		if err != nil {
			if errors.Is(err, errTenantUnassigned) {
				recordToolAliasUses(r.Context())
				mcpHandler.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
				return
			}
			if errors.Is(err, errIdentityUnavailable) {
				log.Printf("tenant MCP identity authority unavailable: %v", err)
				writeJSON(w, 503, map[string]string{"error": "identity_unavailable"})
				return
			}
			if errors.Is(err, errInvalidToken) {
				log.Print("tenant MCP rejected bearer during GoTrue verification")
				challenge()
				writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid_token"})
				return
			}
			log.Print("tenant MCP rejected verified bearer during tenant resolution")
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
			return
		}
		recordToolAliasUses(r.Context())
		mcpHandler.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, id)))
	})
	return mux
}

func (s *server) adminHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/dynamic-skills", s.skillAdminHTTP)
	return mux
}

func muxHandlerRedirect(w http.ResponseWriter, r *http.Request, path string, mux *http.ServeMux) {
	clone := r.Clone(r.Context())
	clone.URL.Path = path
	mux.ServeHTTP(w, clone)
}

func (s *server) exchangeToken(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 131072)
	if err := r.ParseForm(); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_request"})
		return
	}
	const grant = "urn:ietf:params:oauth:grant-type:token-exchange"
	const tokenType = "urn:ietf:params:oauth:token-type:access_token"
	if r.Form.Get("grant_type") != grant || (r.Form.Get("subject_token_type") != "" && r.Form.Get("subject_token_type") != tokenType) {
		writeJSON(w, 400, map[string]string{"error": "unsupported_grant_type"})
		return
	}
	audience := os.Getenv("RE8CH_TOOL_AUDIENCE")
	if audience == "" {
		audience = "re8ch-tenant-tool"
	}
	if r.Form.Get("audience") != audience {
		writeJSON(w, 401, map[string]string{"error": "invalid_target"})
		return
	}
	token := r.Form.Get("subject_token")
	if _, err := s.verifySupabase(r.Context(), token); err != nil {
		if errors.Is(err, errIdentityUnavailable) {
			writeJSON(w, 503, map[string]string{"error": "identity_unavailable"})
			return
		}
		writeJSON(w, 401, map[string]string{"error": "invalid_subject_token"})
		return
	}
	claims, err := decodeClaims(token)
	if err != nil {
		writeJSON(w, 401, map[string]string{"error": "invalid_subject_token"})
		return
	}
	expires := min(max(claims.Expires-time.Now().Unix(), 1), 600)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Pragma", "no-cache")
	writeJSON(w, 200, map[string]any{"access_token": token, "issued_token_type": tokenType, "token_type": "Bearer", "expires_in": expires})
}

func billingHeaders(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Vary", "Origin")
	if r.Header.Get("Origin") == "https://auth.re8ch.com" {
		w.Header().Set("Access-Control-Allow-Origin", "https://auth.re8ch.com")
	}
}

func (s *server) billingHTTP(w http.ResponseWriter, r *http.Request) {
	billingHeaders(w, r)
	selected := r.Header.Get("X-RE8CH-Tenant")
	if selected == "" || selected == "admin" || !tenantName.MatchString(selected) {
		writeJSON(w, 400, map[string]string{"error": "select an organization"})
		return
	}
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	bearer := strings.TrimPrefix(header, "Bearer ")
	id, err := s.authenticate(r.Context(), bearer, selected)
	if errors.Is(err, errIdentityUnavailable) {
		writeJSON(w, 503, map[string]string{"error": "identity_unavailable"})
		return
	}
	if errors.Is(err, errInvalidToken) || id.Service {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	if err != nil {
		writeJSON(w, 403, map[string]string{"error": "organization access denied"})
		return
	}
	now := time.Now().UTC()
	start, end := now.Add(-24*time.Hour), now
	if value := r.URL.Query().Get("from"); value != "" {
		start, err = parseTime(value)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid billing window"})
			return
		}
	}
	if value := r.URL.Query().Get("to"); value != "" {
		end, err = parseTime(value)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid billing window"})
			return
		}
	}
	result, err := s.billingUsage(id.Tenant, start, end)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid billing window"})
		return
	}
	writeJSON(w, 200, result)
}

func (s *server) receiveUsageHTTP(w http.ResponseWriter, r *http.Request) {
	allowed, err := usageCollectorAuthenticated(r)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "usage authentication unavailable"})
		return
	}
	if !allowed {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 65536)
	var event map[string]any
	if err := json.NewDecoder(r.Body).Decode(&event); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid UsageEvent"})
		return
	}
	result, err := s.receiveUsage(event)
	if err != nil {
		status := 400
		if strings.Contains(err.Error(), "different content") {
			status = 409
		}
		writeJSON(w, status, map[string]string{"error": err.Error()})
		return
	}
	status := 201
	if result["idempotentReplay"] == true {
		status = 200
	}
	writeJSON(w, status, result)
}

func (s *server) skillAdminHTTP(w http.ResponseWriter, r *http.Request) {
	secret := os.Getenv("DYNAMIC_SKILL_ADMIN_TOKEN")
	header := r.Header.Get("Authorization")
	if secret == "" || !strings.HasPrefix(header, "Bearer ") || subtle.ConstantTimeCompare([]byte(strings.TrimPrefix(header, "Bearer ")), []byte(secret)) != 1 {
		writeJSON(w, 401, map[string]string{"error": "unauthorized"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 50000)
	var command request
	if err := json.NewDecoder(r.Body).Decode(&command); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	command.Tenant = "admin"
	command.Subject = strings.TrimSpace(r.Header.Get("X-RE8CH-Admin-Subject"))
	if command.Subject == "" || len(command.Subject) > 256 {
		writeJSON(w, 400, map[string]string{"error": "admin subject required"})
		return
	}
	var result any
	var err error
	switch command.Action {
	case "pending":
		result, err = s.pending()
	case "publish":
		result, err = s.submit(command, true)
	case "review":
		result, err = s.review(command)
	case "revoke":
		result, err = s.revoke(command)
	default:
		writeJSON(w, 400, map[string]string{"error": "unknown action"})
		return
	}
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, 200, result)
}

func main() {
	path := os.Getenv("DYNAMIC_SKILL_DB")
	if path == "" {
		path = "/var/lib/tenant-platform/dynamic-skills.db"
	}
	s, err := open(path)
	if err != nil {
		log.Fatal(err)
	}
	defer s.db.Close()
	ledgerPath := os.Getenv("TENANT_SERVICE_DB")
	if ledgerPath == "" {
		ledgerPath = "/var/lib/tenant-platform/tenant-services.db"
	}
	s.ledger, err = openLedger(ledgerPath)
	if err != nil {
		log.Fatal(err)
	}
	defer s.ledger.Close()
	redemptionPath := os.Getenv("DEVICE_REGISTRY_PATH")
	if redemptionPath == "" {
		redemptionPath = "/var/lib/tenant-platform/tenant-devices.db"
	}
	s.redemptions, err = openRedemptions(redemptionPath)
	if err != nil {
		log.Fatal(err)
	}
	defer s.redemptions.Close()
	host := os.Getenv("LISTEN_HOST")
	if host == "" {
		host = "0.0.0.0"
	}
	port := os.Getenv("LISTEN_PORT")
	if _, err := strconv.Atoi(port); err != nil {
		port = "19448"
	}
	httpServer := &http.Server{Addr: host + ":" + port, Handler: s.handler(), ReadHeaderTimeout: 5 * time.Second}
	adminPort := os.Getenv("ADMIN_LISTEN_PORT")
	if _, err := strconv.Atoi(adminPort); err != nil {
		adminPort = "19450"
	}
	adminServer := &http.Server{Addr: host + ":" + adminPort, Handler: s.adminHandler(), ReadHeaderTimeout: 5 * time.Second}
	s.moduleRegistry()
	defer s.modules.close()
	shutdown, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	failures := make(chan error, 2)
	go func() { failures <- adminServer.ListenAndServe() }()
	go func() { failures <- httpServer.ListenAndServe() }()
	select {
	case <-shutdown.Done():
	case err := <-failures:
		if !errors.Is(err, http.ErrServerClosed) {
			log.Printf("tenant listener failed: %v", err)
		}
	}
	// Shutdown both listeners together, allowing accepted mutations to finish.
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	adminDone := make(chan struct{})
	go func() { defer close(adminDone); _ = adminServer.Shutdown(ctx) }()
	if err := httpServer.Shutdown(ctx); err != nil {
		log.Printf("tenant drain deadline: %v", err)
	}
	<-adminDone
}
