package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestPlatformAdminMembershipIsLiveAndFailClosed(t *testing.T) {
	active := true
	rpc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer subject-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if active {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "admin", "tenant_role": "admin"}})
		} else {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "re8ch", "tenant_role": "admin"}})
		}
	}))
	defer rpc.Close()
	t.Setenv("PLATFORM_MEMBERSHIP_URL", rpc.URL)
	allowed, err := currentPlatformAdmin(context.Background(), "subject-token")
	if err != nil || !allowed {
		t.Fatalf("active platform admin denied: %v", err)
	}
	active = false
	allowed, err = currentPlatformAdmin(context.Background(), "subject-token")
	if err != nil || allowed {
		t.Fatalf("revoked platform admin allowed: %v", err)
	}
	allowed, err = currentPlatformAdmin(context.Background(), "other-token")
	if err == nil || allowed {
		t.Fatal("unauthorized RPC call allowed")
	}
}

func TestPlatformBackendMCPProxyPreservesAuthAndResult(t *testing.T) {
	backend := mcp.NewServer(&mcp.Implementation{Name: "fixture-admin", Version: "1.0.0"}, nil)
	mcp.AddTool(backend, &mcp.Tool{Name: "tenant_list", Description: "List tenants"},
		func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			return nil, map[string]any{"tenants": []string{"re8ch"}}, nil
		})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-service-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return backend }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}).ServeHTTP(w, r)
	}))
	defer upstream.Close()
	t.Setenv("SERVICE_API_TOKEN", "fixture-service-token")
	t.Setenv("PLATFORM_TENANT_ADMIN_MCP_URL", upstream.URL)
	value, err := withPlatformBackend(context.Background(), "tenant_admin", func(session *mcp.ClientSession) (any, error) {
		return session.CallTool(context.Background(), &mcp.CallToolParams{Name: "tenant_list", Arguments: map[string]any{}})
	})
	if err != nil {
		t.Fatal(err)
	}
	result := value.(*mcp.CallToolResult)
	if result.IsError || result.StructuredContent == nil {
		t.Fatalf("upstream result lost: %#v", result)
	}
}

func TestPlatformResourceArgvIsBoundedAndSecretSafe(t *testing.T) {
	discover, err := platformResourceArgv(platformResourceReadInput{Action: "discover"})
	if err != nil || strings.Join(discover, " ") != "api-resources -o wide" {
		t.Fatalf("unexpected discovery argv: %v %v", discover, err)
	}
	listed, err := platformResourceArgv(platformResourceReadInput{Action: "list", Resource: "deployments.apps", AllNamespaces: true, LabelSelector: "app=web"})
	if err != nil || strings.Join(listed, " ") != "get deployments.apps --all-namespaces --selector=app=web --show-managed-fields=false -o json" {
		t.Fatalf("unexpected list argv: %v %v", listed, err)
	}
	if _, err := platformResourceArgv(platformResourceReadInput{Action: "get", Resource: "secrets", Name: "database"}); err == nil || !strings.Contains(err.Error(), "Secret values") {
		t.Fatalf("Secret read was accepted: %v", err)
	}
	if _, err := platformResourceArgv(platformResourceReadInput{Action: "list", Resource: "pods", Namespace: "default", AllNamespaces: true}); err == nil {
		t.Fatal("conflicting namespace selectors were accepted")
	}
}

func TestPlatformPostgresSystemAllowlist(t *testing.T) {
	for _, database := range []string{"re8ch_identity", "re8ch_billing"} {
		if err := validatePlatformPostgresInput(map[string]any{"database": database, "sql": "select 1"}); err != nil {
			t.Fatalf("allowed system database %s rejected: %v", database, err)
		}
	}
	if err := validatePlatformPostgresInput(map[string]any{"database": "postgres", "sql": "select 1"}); err == nil {
		t.Fatal("non-system database accepted")
	}
	if err := validatePlatformPostgresInput(map[string]any{"database": "re8ch_identity", "sql": "select 1", "password": "no"}); err == nil {
		t.Fatal("credential input accepted")
	}
}

func TestPlatformStalwartAdminRoutesOnlyBoundedOperations(t *testing.T) {
	name, arguments, err := platformStalwartArguments(platformStalwartAdminInput{
		Action: "read", Object: "Account", ID: "agent-one", Limit: 10,
	})
	if err != nil || name != "stalwart.admin.read" || arguments["object"] != "Account" || arguments["id"] != "agent-one" || arguments["limit"] != 10 {
		t.Fatalf("unexpected read routing: %s %#v %v", name, arguments, err)
	}
	name, arguments, err = platformStalwartArguments(platformStalwartAdminInput{
		Action: "plan", Object: "Account", ID: "agent-one", Patch: map[string]any{"description": "Planning agent"},
	})
	if err != nil || name != "stalwart.admin.plan" || arguments["patch"] == nil {
		t.Fatalf("unexpected plan routing: %s %#v %v", name, arguments, err)
	}
	name, arguments, err = platformStalwartArguments(platformStalwartAdminInput{Action: "apply", PlanHash: strings.Repeat("a", 64)})
	if err != nil || name != "stalwart.admin.apply" || arguments["planHash"] != strings.Repeat("a", 64) {
		t.Fatalf("unexpected apply routing: %s %#v %v", name, arguments, err)
	}
	for _, input := range []platformStalwartAdminInput{
		{Action: "read"},
		{Action: "plan", Object: "Account", ID: "agent-one"},
		{Action: "apply"},
		{Action: "delete", Object: "Account", ID: "agent-one"},
	} {
		if _, _, err := platformStalwartArguments(input); err == nil {
			t.Fatalf("unsafe or incomplete Stalwart operation accepted: %#v", input)
		}
	}
}

func TestExplicitPlatformToolsAreIdentityScoped(t *testing.T) {
	s := testServer(t)
	var ledgerErr error
	s.ledger, ledgerErr = openLedger(t.TempDir() + "/ledger.db")
	if ledgerErr != nil {
		t.Fatal(ledgerErr)
	}
	t.Cleanup(func() { s.ledger.Close() })
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, _ := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		writeJSON(w, 200, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"tenantRef": "admin", "role": "admin", "defaultNamespace": "admin-default", "byocAllowed": false, "nativeAccess": true, "hostingMode": "platform-managed"})
	}))
	defer control.Close()
	active := true
	rpcCalls := 0
	rpc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rpcCalls++
		claims, _ := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if active && claims.Subject == "admin-user" {
			writeJSON(w, 200, []map[string]string{{"tenant_ref": "admin", "tenant_role": "admin"}})
			return
		}
		writeJSON(w, 200, []map[string]string{})
	}))
	defer rpc.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "claim-secret")
	t.Setenv("PLATFORM_MEMBERSHIP_URL", rpc.URL)
	backend := mcp.NewServer(&mcp.Implementation{Name: "fixture-admin", Version: "1.0.0"}, nil)
	mcp.AddTool(backend, &mcp.Tool{Name: "tenant_list"}, func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
		return nil, map[string]any{"tenants": []string{"re8ch"}}, nil
	})
	var authenticationInput controlPlaneAuthenticationInput
	var kubectlArgv []string
	var kubectlTimeout int
	var kubectlCalls int
	var nativeOperation string
	var kubectlCluster, kubectlBinding string
	currentBinding := strings.Repeat("a", 64)
	mcp.AddTool(backend, &mcp.Tool{Name: "clusters.resolve"}, func(_ context.Context, _ *mcp.CallToolRequest, input struct {
		ClusterRef string `json:"clusterRef"`
	}) (*mcp.CallToolResult, any, error) {
		if input.ClusterRef != "re8ch" && input.ClusterRef != "re8ch-atlantic" {
			return &mcp.CallToolResult{IsError: true}, nil, nil
		}
		return nil, map[string]any{"clusterRef": input.ClusterRef, "bindingDigest": currentBinding}, nil
	})
	mcp.AddTool(backend, &mcp.Tool{Name: "clusters.list"}, func(_ context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
		return nil, map[string]any{"clusters": []map[string]any{{"clusterRef": "re8ch", "bindingDigest": currentBinding, "state": "Ready", "adapter": "local", "clusterIdentityRef": map[string]any{"namespace": "pilot", "name": "re8ch", "uid": "fixture-self"}}, {"clusterRef": "re8ch-atlantic", "bindingDigest": currentBinding, "state": "Ready", "adapter": "among-broker", "serviceIdentity": "spiffe://fixture/ns/default/service/kubernetes"}}}, nil
	})
	mcp.AddTool(backend, &mcp.Tool{Name: "kubectl.exec"}, func(_ context.Context, _ *mcp.CallToolRequest, input platformKubectlInput) (*mcp.CallToolResult, any, error) {
		kubectlCalls++
		kubectlArgv, kubectlTimeout = input.Argv, input.Timeout
		kubectlCluster, kubectlBinding = input.ClusterRef, input.ClusterBindingDigest
		return nil, map[string]any{"exitCode": 0, "stdout": "ok", "auditRef": "audit://kubectl/test"}, nil
	})
	mcp.AddTool(backend, &mcp.Tool{Name: "nodes.list"}, func(_ context.Context, _ *mcp.CallToolRequest, _ struct {
		ClusterRef           string `json:"clusterRef,omitempty"`
		ClusterBindingDigest string `json:"clusterBindingDigest,omitempty"`
	}) (*mcp.CallToolResult, any, error) {
		return nil, map[string]any{"nodes": []string{"r640"}}, nil
	})
	for _, name := range []string{"kube.resource.apply", "kube.resource.delete"} {
		name := name
		mcp.AddTool(backend, &mcp.Tool{Name: name}, func(_ context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
			nativeOperation = name
			return nil, map[string]any{"operation": name, "arguments": input, "auditRef": "audit://kube/test"}, nil
		})
	}
	mcp.AddTool(backend, &mcp.Tool{Name: "reconcile_control_plane_authentication_config"}, func(_ context.Context, _ *mcp.CallToolRequest, input controlPlaneAuthenticationInput) (*mcp.CallToolResult, any, error) {
		authenticationInput = input
		return nil, map[string]any{"applied": input.Apply, "commitSha": input.CommitSHA, "planHash": input.PlanHash}, nil
	})
	upstream := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return backend }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true}))
	defer upstream.Close()
	t.Setenv("SERVICE_API_TOKEN", "fixture-service-token")
	t.Setenv("PLATFORM_TENANT_ADMIN_MCP_URL", upstream.URL)
	originalNative := platformBackends["native"]
	platformBackends["native"] = platformBackend{
		URL: upstream.URL, Namespace: "tenant-infrastructure", Secret: "admin-vmcp-backend-auth", SecretKey: "token", Header: "Authorization",
	}
	defer func() { platformBackends["native"] = originalNative }()
	callPath := func(path, subject, session, method string, params any) map[string]any {
		claims, _ := json.Marshal(map[string]any{"iss": supabaseIssuer, "aud": "authenticated", "sub": subject, "session_id": session, "exp": time.Now().Add(time.Hour).Unix()})
		token := "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		req := httptest.NewRequest("POST", path, strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != 200 {
			t.Fatalf("MCP HTTP %d: %s", response.Code, response.Body.String())
		}
		var result map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	call := func(subject, session, method string, params any) map[string]any {
		return callPath("/mcp", subject, session, method, params)
	}
	contains := func(result map[string]any, name string) bool {
		tools := result["result"].(map[string]any)["tools"].([]any)
		for _, item := range tools {
			if item.(map[string]any)["name"] == name {
				return true
			}
		}
		return false
	}
	adminTools := call("admin-user", "session-1", "tools/list", map[string]any{})
	if contains(adminTools, "platform_tool_catalog") || contains(adminTools, "platform_tool_call") || !contains(adminTools, "platform_kubernetes_resource_read") || !contains(adminTools, "platform_kubernetes_resource_apply") || !contains(adminTools, "platform_kubernetes_resource_delete") || !contains(adminTools, "platform_nodes_list") || !contains(adminTools, "platform_kubectl_exec") || !contains(adminTools, "reconcile_control_plane_authentication_config") {
		t.Fatal("admin tool absent")
	}
	pluginTools := callPath("/plugin/mcp", "admin-user", "session-1", "tools/list", map[string]any{})
	if contains(pluginTools, "platform_tool_catalog") || contains(pluginTools, "platform_tool_call") || !contains(pluginTools, "platform_kubectl_exec") {
		t.Fatal("plugin publication exposed a generic bridge or omitted explicit kubectl")
	}
	pluginLegacyCall := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_tool_call", "arguments": map[string]any{"backend": "tenant_admin", "toolName": "tenant_list"}})
	if pluginLegacyCall["error"] == nil && pluginLegacyCall["result"].(map[string]any)["isError"] != true {
		t.Fatal("unpublished generic bridge was callable through plugin path")
	}
	pluginKubectl := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubectl_exec", "arguments": map[string]any{"argv": []string{"get", "--raw=/version"}}})
	if pluginKubectl["error"] != nil || pluginKubectl["result"].(map[string]any)["isError"] == true || strings.Join(kubectlArgv, " ") != "get --raw=/version" {
		t.Fatalf("plugin kubectl argv was not routed: %#v; argv=%v", pluginKubectl, kubectlArgv)
	}

	// Use only the old published argv/timeout schema, including actual SSA verbs.
	for _, ref := range []string{"re8ch", "re8ch-atlantic"} {
		for _, dryRun := range []bool{true, false} {
			argv := []string{"--context=" + ref, "apply", "--server-side", "--field-manager=fixture-acceptance", "-f", "https://example.invalid/reviewed/fixture.yaml"}
			expected := append([]string{}, argv[1:]...)
			if dryRun {
				argv = append(argv, "--dry-run=server")
				expected = append(expected, "--dry-run=server")
			}
			reply := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubectl_exec", "arguments": map[string]any{"argv": argv, "timeout": 20}})
			if reply["error"] != nil || reply["result"].(map[string]any)["isError"] == true || kubectlCluster != ref || kubectlBinding != currentBinding || strings.Join(kubectlArgv, " ") != strings.Join(expected, " ") {
				t.Fatalf("argv-only SSA lost binding or forwarded selector: %#v target=%s binding=%s argv=%v", reply, kubectlCluster, kubectlBinding, kubectlArgv)
			}
		}
	}
	for _, args := range []map[string]any{
		{"argv": []string{"--context=unknown", "get", "pods"}},
		{"argv": []string{"--context=re8ch", "--context=re8ch-atlantic", "get", "pods"}},
		{"argv": []string{"--context=re8ch-atlantic", "get", "pods"}, "clusterRef": "re8ch"},
		{"argv": []string{"--context", "get"}},
	} {
		before := kubectlCalls
		denied := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubectl_exec", "arguments": args})
		if denied["error"] == nil && denied["result"].(map[string]any)["isError"] != true || kubectlCalls != before {
			t.Fatalf("invalid/unknown context reached business command: %#v calls=%d before=%d", denied, kubectlCalls, before)
		}
	}
	pluginResources := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_read", "arguments": map[string]any{"action": "discover"}})
	if pluginResources["error"] != nil || pluginResources["result"].(map[string]any)["isError"] == true || strings.Join(kubectlArgv, " ") != "api-resources -o wide" || kubectlTimeout != 120 {
		t.Fatalf("platform resource discovery was not routed: %#v; argv=%v timeout=%d", pluginResources, kubectlArgv, kubectlTimeout)
	}
	manifest := map[string]any{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "meta-probe", "namespace": "ops"}, "data": map[string]any{"status": "ready"}}
	bindings := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_cluster_bindings_list", "arguments": map[string]any{}})
	if bindings["error"] != nil || bindings["result"].(map[string]any)["isError"] == true {
		t.Fatalf("cluster directory unavailable: %#v", bindings)
	}
	byoc := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "byoc_connection_list", "arguments": map[string]any{}})
	if byoc["error"] != nil || byoc["result"].(map[string]any)["isError"] == true {
		t.Fatalf("Admin BYOC view unavailable: %#v", byoc)
	}
	encoded, _ := json.Marshal(byoc)
	if !strings.Contains(string(encoded), "managed-cluster://admin/re8ch-atlantic") || !strings.Contains(string(encoded), "managed-cluster://admin/re8ch") {
		t.Fatalf("Admin BYOC directory omitted managed targets: %s", encoded)
	}

	// Execute the existing directory's exact examples without any new input key.
	connectionView := byoc["result"].(map[string]any)["structuredContent"].(map[string]any)
	for _, raw := range connectionView["connections"].([]any) {
		connection := raw.(map[string]any)
		if connection["discovery_tool"] != "byoc_connection_list" {
			t.Fatalf("directory references unrefreshed tool: %#v", connection)
		}
		operation := connection["kubectl"].(map[string]any)
		reply := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": operation["tool"], "arguments": operation["arguments"]})
		if reply["error"] != nil || reply["result"].(map[string]any)["isError"] == true || kubectlCluster != connection["cluster_ref"] || kubectlBinding != currentBinding || strings.Join(kubectlArgv, " ") != "get --raw=/version" {
			t.Fatalf("directory example did not resolve exactly its target: %#v %#v", connection, reply)
		}
	}
	var registrations int
	if err := s.ledger.QueryRow("SELECT COUNT(*) FROM cluster_registrations").Scan(&registrations); err != nil || registrations != 0 {
		t.Fatalf("managed view created duplicate registrations: %d %v", registrations, err)
	}
	for _, ref := range []string{"re8ch", "re8ch-atlantic"} {
		read := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_read", "arguments": map[string]any{"action": "discover", "clusterRef": ref}})
		if read["error"] != nil || read["result"].(map[string]any)["isError"] == true || kubectlCluster != ref || kubectlBinding != currentBinding {
			t.Fatalf("target read did not preserve binding: %#v %s %s", read, kubectlCluster, kubectlBinding)
		}
	}
	remotePreview := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_apply", "arguments": map[string]any{"manifest": manifest, "clusterRef": "re8ch-atlantic"}})
	if remotePreview["error"] != nil || remotePreview["result"].(map[string]any)["isError"] == true || kubectlCluster != "re8ch-atlantic" {
		t.Fatalf("remote preview failed: %#v", remotePreview)
	}
	remotePlan, _ := platformMutationPlan("apply", map[string]any{"manifest": manifest, "cluster": map[string]any{"clusterRef": "re8ch-atlantic", "clusterBindingDigest": currentBinding}})
	for _, ref := range []string{"re8ch", "unknown"} {
		before := nativeOperation
		denied := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_apply", "arguments": map[string]any{"manifest": manifest, "clusterRef": ref, "apply": true, "planHash": remotePlan}})
		if denied["error"] == nil && denied["result"].(map[string]any)["isError"] != true || nativeOperation != before {
			t.Fatalf("cross-target plan reached apply: %#v", denied)
		}
	}
	currentBinding = strings.Repeat("b", 64)
	stale := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_apply", "arguments": map[string]any{"manifest": manifest, "clusterRef": "re8ch-atlantic", "apply": true, "planHash": remotePlan}})
	if stale["error"] == nil && stale["result"].(map[string]any)["isError"] != true {
		t.Fatalf("stale binding plan accepted: %#v", stale)
	}
	preview := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubernetes_resource_apply", "arguments": map[string]any{"manifest": manifest}})
	if preview["error"] != nil || preview["result"].(map[string]any)["isError"] == true || !strings.Contains(strings.Join(kubectlArgv, " "), "--dry-run=server") {
		t.Fatalf("platform resource apply preview was not server validated: %#v; argv=%v", preview, kubectlArgv)
	}
	legacyRead := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "tenant_kubernetes_get", "arguments": map[string]any{"kind": "gateways.gateway.networking.k8s.io"}})
	if legacyRead["error"] != nil || legacyRead["result"].(map[string]any)["isError"] == true || strings.Join(kubectlArgv, " ") != "get gateways.gateway.networking.k8s.io --all-namespaces --show-managed-fields=false -o json" {
		t.Fatalf("existing Kubernetes read interface did not receive Meta scope: %#v; argv=%v", legacyRead, kubectlArgv)
	}
	legacyApply := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "tenant_kubernetes_apply", "arguments": map[string]any{"object": manifest, "apply": true}})
	if legacyApply["error"] != nil || legacyApply["result"].(map[string]any)["isError"] == true || nativeOperation != "kube.resource.apply" {
		t.Fatalf("existing Kubernetes apply interface did not use the native Meta backend: %#v; operation=%s", legacyApply, nativeOperation)
	}
	legacyDelete := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "tenant_kubernetes_delete", "arguments": map[string]any{"kind": "configmaps", "name": "meta-probe", "namespace": "ops", "apply": true}})
	if legacyDelete["error"] != nil || legacyDelete["result"].(map[string]any)["isError"] == true || nativeOperation != "kube.resource.delete" {
		t.Fatalf("existing Kubernetes delete interface did not use the native Meta backend: %#v; operation=%s", legacyDelete, nativeOperation)
	}
	pluginNodes := callPath("/plugin/mcp", "admin-user", "session-1", "tools/call", map[string]any{"name": "platform_nodes_list", "arguments": map[string]any{}})
	if pluginNodes["error"] != nil || pluginNodes["result"].(map[string]any)["isError"] == true {
		t.Fatalf("platform node inventory was not routed: %#v", pluginNodes)
	}
	for _, listed := range adminTools["result"].(map[string]any)["tools"].([]any) {
		tool := listed.(map[string]any)
		if tool["name"] != "platform_kubectl_exec" {
			continue
		}
		properties := tool["inputSchema"].(map[string]any)["properties"].(map[string]any)
		argv := properties["argv"].(map[string]any)
		if argv["type"] != "array" || argv["items"].(map[string]any)["type"] != "string" {
			t.Fatalf("kubectl argv schema is not a string array: %#v", argv)
		}
	}
	if rpcCalls != 1 {
		t.Fatalf("first session made %d membership calls", rpcCalls)
	}
	assertPlatformStatus := func(subject, session string, allowed bool) {
		response := call(subject, session, "tools/call", map[string]any{"name": "tenant_access_status", "arguments": map[string]any{}})
		status := response["result"].(map[string]any)["structuredContent"].(map[string]any)
		if status["platformAdminAuthorized"] != allowed || status["platformToolsAvailable"] != allowed {
			t.Fatalf("wrong platform diagnostic for %s: %#v", subject, status)
		}
		if allowed && !strings.Contains(status["platformToolDiscovery"].(string), "Backend authorization does not prove plugin publication") {
			t.Fatalf("platform diagnostic implies unavailable tools are callable: %#v", status)
		}
	}
	assertPlatformStatus("admin-user", "session-1", true)
	kubectl := call("admin-user", "session-1", "tools/call", map[string]any{"name": "platform_kubectl_exec", "arguments": map[string]any{"argv": []string{"get", "--raw=/readyz"}}})
	if kubectl["error"] != nil || kubectl["result"].(map[string]any)["isError"] == true || strings.Join(kubectlArgv, " ") != "get --raw=/readyz" || kubectlTimeout != 30 {
		t.Fatalf("explicit kubectl argv was not routed unchanged: %#v; argv=%v timeout=%d", kubectl, kubectlArgv, kubectlTimeout)
	}
	direct := call("admin-user", "session-1", "tools/call", map[string]any{"name": "reconcile_control_plane_authentication_config", "arguments": map[string]any{"commitSha": strings.Repeat("a", 40), "apply": true, "planHash": strings.Repeat("b", 64)}})
	if direct["error"] != nil || direct["result"].(map[string]any)["isError"] == true {
		t.Fatalf("direct authentication reconcile failed: %#v", direct)
	}
	if authenticationInput.CommitSHA != strings.Repeat("a", 40) || !authenticationInput.Apply || authenticationInput.PlanHash != strings.Repeat("b", 64) {
		t.Fatalf("direct authentication arguments changed: %#v", authenticationInput)
	}
	if rpcCalls != 1 {
		t.Fatalf("same-session call repeated membership lookup: %d", rpcCalls)
	}
	ordinaryTools := call("ordinary-user", "session-2", "tools/list", map[string]any{})
	if contains(ordinaryTools, "platform_tool_catalog") || contains(ordinaryTools, "platform_tool_call") || !contains(ordinaryTools, "platform_kubectl_exec") || !contains(ordinaryTools, "reconcile_control_plane_authentication_config") {
		t.Fatal("platform entry point missing from stable tool list")
	}
	assertPlatformStatus("ordinary-user", "session-2", false)
	deniedKubectl := call("ordinary-user", "session-2", "tools/call", map[string]any{"name": "platform_kubectl_exec", "arguments": map[string]any{"argv": []string{"--context=re8ch-atlantic", "get", "pods"}}})
	if deniedKubectl["error"] == nil && deniedKubectl["result"].(map[string]any)["isError"] != true {
		t.Fatal("ordinary user called explicit kubectl tool")
	}
	deniedDirect := call("ordinary-user", "session-2", "tools/call", map[string]any{"name": "reconcile_control_plane_authentication_config", "arguments": map[string]any{"commitSha": strings.Repeat("a", 40)}})
	if deniedDirect["error"] == nil && deniedDirect["result"].(map[string]any)["isError"] != true {
		t.Fatal("ordinary user called direct authentication reconciler")
	}
	if rpcCalls != 2 {
		t.Fatalf("ordinary session repeated membership lookup: %d", rpcCalls)
	}
	active = false
	if !contains(call("admin-user", "session-1", "tools/list", map[string]any{}), "reconcile_control_plane_authentication_config") || rpcCalls != 2 {
		t.Fatal("existing login session lost its cached admin decision")
	}
	if !contains(call("admin-user", "session-3", "tools/list", map[string]any{}), "reconcile_control_plane_authentication_config") || rpcCalls != 3 {
		t.Fatal("new login session lost stable platform entry points")
	}
	assertPlatformStatus("admin-user", "session-3", false)
}
