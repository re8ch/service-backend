package main

import (
	"context"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// This is the caller-scoped membership projection used when a session first
// requests tenant tools. A failed read closes the privileged gate.
func currentPlatformAdmin(ctx context.Context, bearer string) (bool, error) {
	if bearer == "" {
		return false, errors.New("user bearer required")
	}
	endpoint := os.Getenv("PLATFORM_MEMBERSHIP_URL")
	if endpoint == "" {
		endpoint = "http://supabase-identity-rest-ro.supabase-identity.svc:3000/rpc/my_tenant_runtime_memberships"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	req.Header.Set("Accept", "application/json")
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("membership RPC returned HTTP %d", resp.StatusCode)
	}
	var memberships []struct {
		Tenant string `json:"tenant_ref"`
		Role   string `json:"tenant_role"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 65536)).Decode(&memberships); err != nil {
		return false, err
	}
	for _, m := range memberships {
		if m.Tenant == "admin" && m.Role == "admin" {
			return true, nil
		}
	}
	return false, nil
}

type platformToolInput struct {
	Backend   string         `json:"backend" jsonschema:"Backend from platform_tool_catalog: cloud, infra, admin, native, or tenant_admin"`
	ToolName  string         `json:"toolName" jsonschema:"Tool name from platform_tool_catalog"`
	Arguments map[string]any `json:"arguments,omitempty" jsonschema:"Arguments matching the selected tool's inputSchema"`
}

type platformBackend struct {
	URL       string          `json:"url"`
	Namespace string          `json:"namespace"`
	Secret    string          `json:"secret"`
	SecretKey string          `json:"secretKey"`
	Header    string          `json:"header"`
	Allowed   map[string]bool `json:"allowed"`
}

func names(text string) map[string]bool {
	result := map[string]bool{}
	for _, name := range strings.Fields(text) {
		result[name] = true
	}
	return result
}

// Defaults are also published as the GitOps module registry. Runtime configuration
// is data: changing an endpoint or allowlist does not require a gateway rebuild.
//
//go:embed platform-modules.json
var defaultPlatformModules []byte

var platformBackends = mustDefaultPlatformBackends()

func mustDefaultPlatformBackends() map[string]platformBackend {
	registry, err := decodePlatformRegistry(defaultPlatformModules)
	if err != nil {
		panic(err)
	}
	return registry.Backends
}

func platformCredential(ctx context.Context, backend platformBackend) (string, error) {
	if backend.Secret == "admin-vmcp-backend-auth" && backend.Namespace == "tenant-infrastructure" {
		value := os.Getenv("SERVICE_API_TOKEN")
		if value == "" {
			return "", errors.New("missing tenant admin backend credential")
		}
		return value, nil
	}
	secret, err := kubeRequest(ctx, http.MethodGet, "/api/v1/namespaces/"+backend.Namespace+"/secrets/"+backend.Secret, nil)
	if err != nil {
		return "", err
	}
	data, ok := secret["data"].(map[string]any)
	if !ok {
		return "", errors.New("invalid platform credential")
	}
	encoded, ok := data[backend.SecretKey].(string)
	if !ok {
		return "", errors.New("missing platform credential")
	}
	value, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(value) == 0 {
		return "", errors.New("invalid platform credential")
	}
	return string(value), nil
}

type platformAuthTransport struct {
	base            http.RoundTripper
	token           string
	header          string
	connecting      *atomic.Bool
	connectDeadline time.Time
}

func (t platformAuthTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	ctx := req.Context()
	cancel := func() {}
	if t.connecting != nil && t.connecting.Load() {
		ctx, cancel = context.WithDeadline(ctx, t.connectDeadline)
	}
	copy := req.Clone(ctx)
	if t.header == "Authorization" {
		copy.Header.Set("Authorization", "Bearer "+t.token)
	} else {
		copy.Header.Set(t.header, t.token)
	}
	response, err := t.base.RoundTrip(copy)
	if err != nil {
		cancel()
		return nil, err
	}
	response.Body = &platformResponseBody{ReadCloser: response.Body, cancel: cancel}
	return response, nil
}

type platformResponseBody struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (b *platformResponseBody) Close() error { defer b.cancel(); return b.ReadCloser.Close() }

func withPlatformBackend(ctx context.Context, name string, fn func(*mcp.ClientSession, map[string]bool) (any, error)) (any, error) {
	registry := newPlatformRegistry("")
	defer registry.close()
	return registry.call(ctx, name, fn)
}

func platformIdentity(ctx context.Context) (identity, error) {
	id, err := identityFrom(ctx)
	if err != nil {
		return identity{}, err
	}
	if !id.PlatformAdmin {
		return identity{}, errors.New("platform admin membership required for this session")
	}
	return id, nil
}

func (s *server) registerPlatformTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_tool_catalog"), Name: "platform_tool_catalog", Description: "List Cloud Platform, Cluster Infra, and platform admin tools available to the current platform administrator."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			return nil, s.moduleRegistry().catalog(ctx), nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_tool_call"), Name: "platform_tool_call", Description: "Call one allowlisted Cloud Platform, Cluster Infra, or platform admin tool. Copy backend, name, and arguments from platform_tool_catalog."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input platformToolInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			backend, ok := s.moduleRegistry().backend(input.Backend)
			if !ok || !backend.Allowed[input.ToolName] {
				return nil, nil, errors.New("platform tool is not allowlisted")
			}
			value, err := s.moduleRegistry().call(ctx, input.Backend, func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
				if !allowed[input.ToolName] {
					return nil, errors.New("platform tool is not allowlisted")
				}
				return session.CallTool(ctx, &mcp.CallToolParams{Name: input.ToolName, Arguments: input.Arguments})
			})
			if err != nil {
				return nil, nil, err
			}
			return value.(*mcp.CallToolResult), nil, nil
		})
}
