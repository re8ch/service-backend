package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// This is the same caller-scoped, live membership projection used by the
// identity service. A missing RPC or failed read closes the privileged gate.
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
	URL       string
	Namespace string
	Secret    string
	SecretKey string
	Header    string
	Allowed   map[string]bool
}

func names(text string) map[string]bool {
	result := map[string]bool{}
	for _, name := range strings.Fields(text) {
		result[name] = true
	}
	return result
}

var platformBackends = map[string]platformBackend{
	"cloud": {
		URL:       "http://mcp-ops-observe-vmcp-internal.toolhive-system.svc.cluster.local:8080/mcp",
		Namespace: "toolhive-system",
		Secret:    "ops-mcp-observe-upstream",
		SecretKey: "x-ops-token",
		Header:    "X-Ops-Token",
		Allowed:   names(`get_usage_guide get_tenant_context list_k8s_resources get_registry_info get_tenant_resource_catalog get_grafana_links get_tenant_metrics_summary get_platform_service_health get_platform_service_catalog get_platform_permission_coverage get_platform_access_posture get_managed_platform_operation_status get_ceph_storage_status get_ready_node_storage_inventory get_runtime_storage_analyses get_nuclio_function_status diagnose_nuclio_function get_nuclio_capacity_status get_litellm_gateway_status get_fleet_control_status get_supabase_platform_status get_neo4j_platform_status get_identity_platform_status get_agent_runtime_status get_observability_platform_status get_artifact_platform_status get_database_routing_status get_external_publication_readiness`),
	},
	"infra": {
		URL:       "http://mcp-ops-operate-vmcp-internal.toolhive-system.svc.cluster.local:8080/mcp",
		Namespace: "toolhive-system",
		Secret:    "ops-mcp-admin-upstream",
		SecretKey: "x-ops-token",
		Header:    "X-Ops-Token",
		Allowed:   names(`get_managed_platform_operation_status restart_managed_platform_deployment scale_managed_platform_deployment get_ceph_recovery_status restart_ceph_osd_1`),
	},
	"admin": {
		URL:       "http://mcp-ops-admin-vmcp-internal.toolhive-system.svc.cluster.local:8080/mcp",
		Namespace: "toolhive-system",
		Secret:    "ops-mcp-admin-upstream",
		SecretKey: "x-ops-token",
		Header:    "X-Ops-Token",
		Allowed:   names(`list_dex_users get_dex_hash_instructions create_dex_user grant_user_namespace get_tenancy_admin_workflow generate_pg_tenant_database_plan ensure_tenant_database_secrets ensure_tenant_database_bundle ensure_tenant_access ensure_harbor_tenant set_tool_publication_stage get_tenant_secret_refs`),
	},
	"native": {
		URL:       "http://re8ch-cloud-platform.ai-operations.svc.cluster.local:19443/mcp",
		Namespace: "ai-operations",
		Secret:    "cluster-infra-service-auth",
		SecretKey: "token",
		Header:    "Authorization",
		Allowed:   names(`nodes.list nodes.get nodes.reachability node.ssh.exec kubectl.exec kube.nodes kube.namespaces kube.pods kube.deployments kube.services kube.endpoints kube.secrets kube.resource.get kube.resource.list kube.resource.watch kube.resource.apply kube.resource.patch kube.resource.delete kube.events.query kube.events.watch workspace.list workspace.status workspace.scan workspace.doctor workspace.index workspace.sync workspace.context workspace.memory-audit`),
	},
	"tenant_admin": {
		URL:       "http://admin-api.tenant-infrastructure.svc.cluster.local/mcp",
		Namespace: "tenant-infrastructure",
		Secret:    "admin-vmcp-backend-auth",
		SecretKey: "token",
		Header:    "Authorization",
		Allowed:   names(`tenant_create tenant_list tenant_grant_create tenant_visibility_update tenant_delete byoc_registration_list byoc_connection_approve byoc_connection_revoke dynamic_skill_pending dynamic_skill_publish dynamic_skill_review dynamic_skill_revoke`),
	},
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
	base   http.RoundTripper
	token  string
	header string
}

func (t platformAuthTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	copy := req.Clone(req.Context())
	if t.header == "Authorization" {
		copy.Header.Set("Authorization", "Bearer "+t.token)
	} else {
		copy.Header.Set(t.header, t.token)
	}
	return t.base.RoundTrip(copy)
}

func withPlatformBackend(ctx context.Context, name string, fn func(*mcp.ClientSession, map[string]bool) (any, error)) (any, error) {
	backend, ok := platformBackends[name]
	if !ok {
		return nil, errors.New("unknown platform backend")
	}
	endpoint := backend.URL
	if override := os.Getenv("PLATFORM_" + strings.ToUpper(name) + "_MCP_URL"); override != "" {
		endpoint = override
	}
	token, err := platformCredential(ctx, backend)
	if err != nil {
		return nil, err
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "re8ch-tenant-platform-gateway", Version: "1.0.0"}, nil)
	httpClient := &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }, Transport: platformAuthTransport{base: http.DefaultTransport, token: token, header: backend.Header}}
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint, HTTPClient: httpClient, DisableStandaloneSSE: true, MaxRetries: -1}, nil)
	if err != nil {
		return nil, err
	}
	defer session.Close()
	return fn(session, backend.Allowed)
}

func platformIdentity(ctx context.Context) (identity, error) {
	id, err := identityFrom(ctx)
	if err != nil {
		return identity{}, err
	}
	allowed, err := currentPlatformAdmin(ctx, id.Token)
	if err != nil || !allowed {
		return identity{}, errors.New("active platform admin membership required")
	}
	return id, nil
}

func (s *server) registerPlatformTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Name: "platform_tool_catalog", Description: "List Cloud Platform, Cluster Infra, and platform admin tools available to the current platform administrator."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			var tools []map[string]any
			var unavailable []string
			for _, backendName := range []string{"cloud", "infra", "admin", "native", "tenant_admin"} {
				value, err := withPlatformBackend(ctx, backendName, func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
					result, err := session.ListTools(ctx, nil)
					if err != nil {
						return nil, err
					}
					var items []map[string]any
					for _, tool := range result.Tools {
						if allowed[tool.Name] {
							items = append(items, map[string]any{"backend": backendName, "name": tool.Name, "description": tool.Description, "inputSchema": tool.InputSchema})
						}
					}
					return items, nil
				})
				if err != nil {
					unavailable = append(unavailable, backendName)
					continue
				}
				tools = append(tools, value.([]map[string]any)...)
			}
			return nil, map[string]any{"tools": tools, "unavailableBackends": unavailable}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Name: "platform_tool_call", Description: "Call one allowlisted Cloud Platform, Cluster Infra, or platform admin tool. Copy backend, name, and arguments from platform_tool_catalog."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input platformToolInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			backend, ok := platformBackends[input.Backend]
			if !ok || !backend.Allowed[input.ToolName] {
				return nil, nil, errors.New("platform tool is not allowlisted")
			}
			if input.Backend != "cloud" {
				id, _ := identityFrom(ctx)
				claims, err := decodeClaims(id.Token)
				if err != nil || claims.AAL != "aal2" {
					return nil, nil, errors.New("authenticator step-up is required for privileged platform tools")
				}
			}
			value, err := withPlatformBackend(ctx, input.Backend, func(session *mcp.ClientSession, allowed map[string]bool) (any, error) {
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
