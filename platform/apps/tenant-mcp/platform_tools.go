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
	"regexp"
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

type platformKubectlInput struct {
	Argv    []string `json:"argv" jsonschema:"kubectl arguments as an array of strings, for example [get,--raw=/version]"`
	Timeout int      `json:"timeout,omitempty" jsonschema:"Execution timeout in seconds, from 1 to 3600; defaults to 30"`
}

type platformResourceReadInput struct {
	Action        string `json:"action"`
	Resource      string `json:"resource,omitempty"`
	Name          string `json:"name,omitempty"`
	Namespace     string `json:"namespace,omitempty"`
	AllNamespaces bool   `json:"allNamespaces,omitempty"`
	LabelSelector string `json:"labelSelector,omitempty"`
	FieldSelector string `json:"fieldSelector,omitempty"`
}

type platformNodeInput struct {
	Name string `json:"name,omitempty"`
}

type platformNodeExecInput struct {
	Name    string   `json:"name"`
	Argv    []string `json:"argv"`
	Timeout int      `json:"timeout,omitempty"`
}

var platformResourceName = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]{0,252}$`)

func callNativePlatformTool(ctx context.Context, s *server, name string, arguments map[string]any) (*mcp.CallToolResult, error) {
	value, err := s.moduleRegistry().call(ctx, "native", func(session *mcp.ClientSession) (any, error) {
		return session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: arguments})
	})
	if err != nil {
		return nil, err
	}
	result, ok := value.(*mcp.CallToolResult)
	if !ok {
		return nil, errors.New("native platform backend returned an invalid result")
	}
	return result, nil
}

func platformResourceArgv(input platformResourceReadInput) ([]string, error) {
	switch input.Action {
	case "discover":
		if input.Resource != "" || input.Name != "" || input.Namespace != "" || input.AllNamespaces || input.LabelSelector != "" || input.FieldSelector != "" {
			return nil, errors.New("discover does not accept resource filters")
		}
		return []string{"api-resources", "-o", "wide"}, nil
	case "list", "get":
	default:
		return nil, errors.New("action must be discover, list, or get")
	}
	resource := strings.ToLower(strings.TrimSpace(input.Resource))
	if !platformResourceName.MatchString(resource) {
		return nil, errors.New("resource must be a Kubernetes API resource name")
	}
	base := strings.Split(resource, ".")[0]
	if base == "secret" || base == "secrets" {
		return nil, errors.New("Secret values are not readable; use tenant_secret_metadata_list for metadata")
	}
	if input.Namespace != "" && !validKubernetesName(input.Namespace) {
		return nil, errors.New("invalid Kubernetes namespace")
	}
	if input.AllNamespaces && input.Namespace != "" {
		return nil, errors.New("namespace and allNamespaces are mutually exclusive")
	}
	if input.Action == "get" && !validKubernetesName(input.Name) {
		return nil, errors.New("get requires a valid resource name")
	}
	if input.Action == "list" && input.Name != "" {
		return nil, errors.New("list does not accept a resource name")
	}
	argv := []string{"get", resource}
	if input.Name != "" {
		argv = append(argv, input.Name)
	}
	if input.AllNamespaces {
		argv = append(argv, "--all-namespaces")
	} else if input.Namespace != "" {
		argv = append(argv, "--namespace", input.Namespace)
	}
	if input.LabelSelector != "" {
		argv = append(argv, "--selector="+input.LabelSelector)
	}
	if input.FieldSelector != "" {
		argv = append(argv, "--field-selector="+input.FieldSelector)
	}
	return append(argv, "--show-managed-fields=false", "-o", "json"), nil
}

type controlPlaneAuthenticationInput struct {
	CommitSHA string `json:"commitSha" jsonschema:"Full reviewed service-backend main commit SHA"`
	Apply     bool   `json:"apply,omitempty" jsonschema:"Apply the exact plan; false performs server-side dry-run"`
	PlanHash  string `json:"planHash,omitempty" jsonschema:"Exact plan hash returned by the dry-run"`
}

type platformBackend struct {
	URL       string `json:"url"`
	Namespace string `json:"namespace"`
	Secret    string `json:"secret"`
	SecretKey string `json:"secretKey"`
	Header    string `json:"header"`
}

// Defaults are also published as the GitOps module registry. Runtime configuration
// is data: changing a backend endpoint does not require a gateway rebuild.
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

func withPlatformBackend(ctx context.Context, name string, fn func(*mcp.ClientSession) (any, error)) (any, error) {
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
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_kubernetes_resource_read"), Name: "platform_kubernetes_resource_read", Description: "Discover every Kubernetes API resource type or read a named/listed non-Secret resource from the directly connected RE8CH platform cluster. Re8ch Admin membership is required. Secret values are never returned.", InputSchema: json.RawMessage(`{"type":"object","properties":{"action":{"type":"string","enum":["discover","list","get"]},"resource":{"type":"string","description":"API resource name such as deployments.apps or ciliumnetworkpolicies.cilium.io"},"name":{"type":"string"},"namespace":{"type":"string"},"allNamespaces":{"type":"boolean"},"labelSelector":{"type":"string"},"fieldSelector":{"type":"string"}},"required":["action"],"additionalProperties":false}`)},
		func(ctx context.Context, _ *mcp.CallToolRequest, input platformResourceReadInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			argv, err := platformResourceArgv(input)
			if err != nil {
				return nil, nil, err
			}
			result, err := callNativePlatformTool(ctx, s, "kubectl.exec", map[string]any{"argv": argv, "timeout": 120})
			return result, nil, err
		})
	for _, definition := range []struct {
		name, backend, description string
	}{
		{"platform_nodes_list", "nodes.list", "List nodes in the directly connected RE8CH platform cluster."},
		{"platform_nodes_get", "nodes.get", "Read one node from the directly connected RE8CH platform cluster."},
		{"platform_nodes_reachability", "nodes.reachability", "Check one platform node's bounded reachability signals."},
	} {
		definition := definition
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(definition.name), Name: definition.name, Description: definition.description},
			func(ctx context.Context, _ *mcp.CallToolRequest, input platformNodeInput) (*mcp.CallToolResult, any, error) {
				if _, err := platformIdentity(ctx); err != nil {
					return nil, nil, err
				}
				arguments := map[string]any{}
				if definition.backend != "nodes.list" {
					if !validKubernetesName(input.Name) {
						return nil, nil, errors.New("valid node name required")
					}
					arguments["name"] = input.Name
				}
				result, err := callNativePlatformTool(ctx, s, definition.backend, arguments)
				return result, nil, err
			})
	}
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_node_ssh_exec"), Name: "platform_node_ssh_exec", Description: "Execute one argv command on a platform node through the existing audited native backend. Re8ch Admin membership is required."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input platformNodeExecInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			if !validKubernetesName(input.Name) || len(input.Argv) == 0 {
				return nil, nil, errors.New("valid node name and argv are required")
			}
			if input.Timeout == 0 {
				input.Timeout = 30
			}
			if input.Timeout < 1 || input.Timeout > 3600 {
				return nil, nil, errors.New("node command timeout must be between 1 and 3600 seconds")
			}
			result, err := callNativePlatformTool(ctx, s, "node.ssh.exec", map[string]any{"name": input.Name, "argv": input.Argv, "timeout": input.Timeout})
			return result, nil, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("reconcile_control_plane_authentication_config"), Name: "reconcile_control_plane_authentication_config", Description: "Plan or apply the reviewed kube-apiserver authentication configuration and its node-local reconciler. Offline control-plane nodes converge when they return; this never restarts K3s."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input controlPlaneAuthenticationInput) (*mcp.CallToolResult, any, error) {
			if _, err := platformIdentity(ctx); err != nil {
				return nil, nil, err
			}
			const backendName = "native"
			const toolName = "reconcile_control_plane_authentication_config"
			arguments := map[string]any{"commitSha": input.CommitSHA, "apply": input.Apply}
			if input.PlanHash != "" {
				arguments["planHash"] = input.PlanHash
			}
			value, err := s.moduleRegistry().call(ctx, backendName, func(session *mcp.ClientSession) (any, error) {
				return session.CallTool(ctx, &mcp.CallToolParams{Name: toolName, Arguments: arguments})
			})
			if err != nil {
				return nil, nil, err
			}
			return value.(*mcp.CallToolResult), nil, nil
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
			result, err := callNativePlatformTool(ctx, s, "kubectl.exec", map[string]any{"argv": input.Argv, "timeout": input.Timeout})
			return result, nil, err
		})
}
