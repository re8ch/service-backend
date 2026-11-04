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

type platformKubectlInput struct {
	Argv    []string `json:"argv" jsonschema:"kubectl arguments as an array of strings, for example [get,--raw=/version]"`
	Timeout int      `json:"timeout,omitempty" jsonschema:"Execution timeout in seconds, from 1 to 3600; defaults to 30"`
}

type controlPlaneAuthenticationInput struct {
	CommitSHA string `json:"commitSha" jsonschema:"Full reviewed service-backend main commit SHA"`
	Apply     bool   `json:"apply,omitempty" jsonschema:"Apply the exact plan; false performs server-side dry-run"`
	PlanHash  string `json:"planHash,omitempty" jsonschema:"Exact plan hash returned by the dry-run"`
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
	if id.Service || id.Tenant == "" || !id.PlatformAdmin {
		return identity{}, errors.New("platform admin membership required for this session")
	}
	return id, nil
}

func (s *server) registerPlatformTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("reconcile_control_plane_authentication_config"), Name: "reconcile_control_plane_authentication_config", Description: "Plan or apply the reviewed kube-apiserver authentication configuration and its node-local reconciler. Offline control-plane nodes converge when they return; this never restarts K3s."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input controlPlaneAuthenticationInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			const backendName = "native"
			const toolName = "reconcile_control_plane_authentication_config"
			backend, ok := s.moduleRegistry().backend(backendName)
			if !ok || !backend.Allowed[toolName] {
				return nil, nil, errors.New("control-plane authentication reconciler is not published")
			}
			arguments := map[string]any{"commitSha": input.CommitSHA, "apply": input.Apply}
			if input.PlanHash != "" {
				arguments["planHash"] = input.PlanHash
			}
			value, err := s.moduleRegistry().call(ctx, backendName, func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
				if !allowed[toolName] {
					return nil, errors.New("control-plane authentication reconciler is not allowlisted")
				}
				return session.CallTool(ctx, &mcp.CallToolParams{Name: toolName, Arguments: arguments})
			})
			if err != nil {
				return nil, nil, err
			}
			return value.(*mcp.CallToolResult), nil, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_tool_catalog"), Name: "platform_tool_catalog", Description: "Read metadata for allowlisted platform tools, including their descriptions and input schemas. This inventory action does not execute any listed operation."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			return nil, s.moduleRegistry().catalog(ctx), nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_kubectl_exec"), Name: "platform_kubectl_exec", Description: "Execute one kubectl command in the RE8CH platform cluster with an argv string array. Requires verified platform administrator membership. Commands may change cluster state; inspect the exact argv and plan material writes first. Returns the backend result and audit reference.", InputSchema: json.RawMessage(`{"type":"object","properties":{"argv":{"type":"array","minItems":1,"items":{"type":"string"},"description":"kubectl arguments, for example [\"get\",\"--raw=/version\"]"},"timeout":{"type":"integer","minimum":1,"maximum":3600,"description":"Execution timeout in seconds; defaults to 30"}},"required":["argv"],"additionalProperties":false}`)},
		func(ctx context.Context, _ *mcp.CallToolRequest, input platformKubectlInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			if len(input.Argv) == 0 {
				return nil, nil, errors.New("kubectl argv is required")
			}
			if input.Timeout == 0 {
				input.Timeout = 30
			}
			if input.Timeout < 1 || input.Timeout > 3600 {
				return nil, nil, errors.New("kubectl timeout must be between 1 and 3600 seconds")
			}
			backend, ok := s.moduleRegistry().backend("native")
			if !ok || !backend.Allowed["kubectl.exec"] {
				return nil, nil, errors.New("kubectl.exec is not allowlisted")
			}
			value, err := s.moduleRegistry().call(ctx, "native", func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
				if !allowed["kubectl.exec"] {
					return nil, errors.New("kubectl.exec is not allowlisted")
				}
				return session.CallTool(ctx, &mcp.CallToolParams{Name: "kubectl.exec", Arguments: map[string]any{"argv": input.Argv, "timeout": input.Timeout}})
			})
			if err != nil {
				return nil, nil, err
			}
			return value.(*mcp.CallToolResult), nil, nil
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
