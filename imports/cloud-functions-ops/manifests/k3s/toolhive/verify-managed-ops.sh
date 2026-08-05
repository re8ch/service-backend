#!/usr/bin/env sh
set -eu

: "${KUBE_SERVER:=https://192.0.2.176:6443}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cleanup() {
  [ -z "${REGISTRY_PF_PID:-}" ] || kill "$REGISTRY_PF_PID" 2>/dev/null || true
  [ -z "${OBSERVE_PF_PID:-}" ] || kill "$OBSERVE_PF_PID" 2>/dev/null || true
  [ -z "${ADMIN_PF_PID:-}" ] || kill "$ADMIN_PF_PID" 2>/dev/null || true
  [ -z "${VM_PF_PID:-}" ] || kill "$VM_PF_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python3 -m py_compile "$SCRIPT_DIR/../ops/ops-mcp-server-app.py"
sh -n "$SCRIPT_DIR/apply-managed-ops.sh" "$SCRIPT_DIR/attach-toolhive-agents.sh"
sh -n "$SCRIPT_DIR/verify-external-publication-readiness.sh"

kubectl --server="$KUBE_SERVER" get --raw=/readyz >/dev/null 2>&1 || \
  fail "Kubernetes API is unreachable at $KUBE_SERVER"

if grep -Eq '^(apiVersion|kind):' "$SCRIPT_DIR/external-observe-publication.template.yaml"; then
  fail "external publication safety template contains applicable Kubernetes objects"
fi
kubectl --server="$KUBE_SERVER" -n toolhive-system wait --for=condition=Ready \
  mcpserver.toolhive.stacklok.dev/ops-observe-external --timeout=60s >/dev/null
external_ingresses=$(kubectl --server="$KUBE_SERVER" get ingress -A -o json | jq '
  [.items[] | select([.spec.rules[]?.host] | index("tools.re8ch.com"))] | length
')
[ "$external_ingresses" -eq 1 ] || fail "tools.re8ch.com must have exactly one reviewed Ingress"
public_tools=$(kubectl --server="$KUBE_SERVER" -n toolhive-system get mcptoolconfig/ops-public-read-tools \
  -o json | jq -c '.spec.toolsFilter')
[ "$public_tools" = '["get_public_tool_service_info"]' ] || fail "external public-read allowlist drifted"

# Keep the storage controller available on a node with its image cached. This
# policy is part of the control-plane contract because ToolHive-managed tools
# and identity dependencies must not silently lose PVC provisioning.
kubectl --server="$KUBE_SERVER" get mutatingadmissionpolicy/local-path-provisioner-availability.re8ch.com >/dev/null
kubectl --server="$KUBE_SERVER" get mutatingadmissionpolicybinding/local-path-provisioner-availability.re8ch.com >/dev/null
local_path_node=$(kubectl --server="$KUBE_SERVER" -n kube-system get deployment local-path-provisioner \
  -o jsonpath='{.spec.template.spec.nodeSelector.kubernetes\.io/hostname}')
[ "$local_path_node" = "vm-20-12-ubuntu" ] || fail "local-path-provisioner is not pinned to the cached system-image node"
kubectl --server="$KUBE_SERVER" -n kube-system wait --for=condition=Available deployment/local-path-provisioner --timeout=60s >/dev/null

# Authentik's writable-primary canary has passed; both identity components and
# the isolated Redis dependency must now remain available.
kubectl --server="$KUBE_SERVER" -n auth wait --for=condition=Available deployment/authentik-server --timeout=60s >/dev/null
kubectl --server="$KUBE_SERVER" -n auth wait --for=condition=Available deployment/authentik-worker --timeout=60s >/dev/null
kubectl --server="$KUBE_SERVER" -n auth wait --for=condition=Available deployment/authentik-redis --timeout=60s >/dev/null
kubectl --server="$KUBE_SERVER" -n auth get networkpolicy authentik-redis >/dev/null

for server in ops-observe-managed ops-operate-managed ops-admin-managed; do
  kubectl --server="$KUBE_SERVER" -n toolhive-system wait \
    --for=condition=Ready mcpserver.toolhive.stacklok.dev/"$server" --timeout=60s >/dev/null
  selected_node=$(kubectl --server="$KUBE_SERVER" -n toolhive-system get deployment "$server" \
    -o jsonpath='{.spec.template.spec.nodeSelector.kubernetes\.io/hostname}')
  [ "$selected_node" = "restricted-node-296f36f2fb" ] || fail "$server proxy is not pinned to restricted-node-296f36f2fb"
done

for remote in toolhive-ops-observe toolhive-ops-operate; do
  accepted=$(kubectl --server="$KUBE_SERVER" -n kagent get remotemcpserver.kagent.dev "$remote" \
    -o json | jq -r '[.status.conditions[] | select(.type == "Accepted") | .status][0]')
  [ "$accepted" = "True" ] || fail "$remote is not Accepted"
done

observe_tools=$(kubectl --server="$KUBE_SERVER" -n kagent get remotemcpserver.kagent.dev toolhive-ops-observe \
  -o json | jq -r '[.status.discoveredTools[].name]')
operate_tools=$(kubectl --server="$KUBE_SERVER" -n kagent get remotemcpserver.kagent.dev toolhive-ops-operate \
  -o json | jq -r '[.status.discoveredTools[].name]')
observe_tool_count=$(printf '%s' "$observe_tools" | jq 'length')
operate_tool_count=$(printf '%s' "$operate_tools" | jq 'length')
[ "$observe_tool_count" -eq 25 ] || fail "observe tool count is not 25"
[ "$operate_tool_count" -eq 26 ] || fail "operate tool count is not 26"
printf '%s' "$observe_tools" | jq -e 'index("get_platform_service_health") != null and index("get_platform_service_catalog") != null and index("get_platform_access_posture") != null and index("get_managed_platform_operation_status") != null and index("get_nuclio_function_status") != null and index("diagnose_nuclio_function") != null and index("get_nuclio_capacity_status") != null and index("get_litellm_gateway_status") != null and index("get_fleet_control_status") != null and index("get_supabase_platform_status") != null and index("get_neo4j_platform_status") != null and index("get_identity_platform_status") != null and index("get_agent_runtime_status") != null and index("get_observability_platform_status") != null and index("get_artifact_platform_status") != null and index("get_database_routing_status") != null and index("get_external_publication_readiness") != null and index("restart_deployment") == null and index("k8s_api_proxy") == null and index("postgres_sql_proxy") == null' >/dev/null || fail "observe allowlist mismatch"
printf '%s' "$operate_tools" | jq -e 'index("get_platform_service_health") != null and index("get_platform_service_catalog") != null and index("get_platform_access_posture") != null and index("get_managed_platform_operation_status") != null and index("get_nuclio_function_status") != null and index("diagnose_nuclio_function") != null and index("get_nuclio_capacity_status") != null and index("get_litellm_gateway_status") != null and index("get_fleet_control_status") != null and index("get_supabase_platform_status") != null and index("get_neo4j_platform_status") != null and index("get_identity_platform_status") != null and index("get_agent_runtime_status") != null and index("get_observability_platform_status") != null and index("get_artifact_platform_status") != null and index("get_database_routing_status") != null and index("get_external_publication_readiness") != null and index("restart_deployment") != null and index("k8s_api_proxy") == null and index("postgres_sql_proxy") == null' >/dev/null || fail "operate allowlist mismatch"
printf '%s' "$observe_tools" | jq -e 'index("get_platform_permission_coverage") != null' >/dev/null || fail "observe permission coverage tool is absent"
printf '%s' "$operate_tools" | jq -e 'index("get_platform_permission_coverage") != null' >/dev/null || fail "operate permission coverage tool is absent"

admin_subject=system:serviceaccount:toolhive-system:ops-mcp-admin
[ "$(kubectl --server="$KUBE_SERVER" auth can-i patch mcpservers.toolhive.stacklok.dev/ops-observe-managed -n toolhive-system --as="$admin_subject")" = yes ] || fail "admin cannot patch approved publication"
[ "$(kubectl --server="$KUBE_SERVER" auth can-i patch mcpservers.toolhive.stacklok.dev/unreviewed-server -n toolhive-system --as="$admin_subject" || true)" = no ] || fail "admin can patch unreviewed publication"
[ "$(kubectl --server="$KUBE_SERVER" auth can-i delete mcpservers.toolhive.stacklok.dev/ops-observe-managed -n toolhive-system --as="$admin_subject" || true)" = no ] || fail "admin can delete a publication"
for target in \
  auth/authentik-server auth/authentik-worker \
  llm-gateway/litellm kagent/kagent-controller \
  opensandbox-system/opensandbox-server fleet/fleet \
  supabase-pg-re8ch/supabase-studio nuclio/nuclio-dashboard \
  codex-platform/codex-session-web; do
  namespace=${target%/*}
  deployment=${target#*/}
  [ "$(kubectl --server="$KUBE_SERVER" auth can-i patch deployments.apps/"$deployment" -n "$namespace" --as="$admin_subject")" = yes ] || fail "admin cannot restart approved platform target $target"
done
[ "$(kubectl --server="$KUBE_SERVER" auth can-i patch deployments.apps/authentik-server -n default --as="$admin_subject" || true)" = no ] || fail "platform restart permission escaped its approved namespace"
[ "$(kubectl --server="$KUBE_SERVER" auth can-i patch deployments.apps/unreviewed -n llm-gateway --as="$admin_subject" || true)" = no ] || fail "admin can restart an unreviewed platform deployment"

kubectl --server="$KUBE_SERVER" -n toolhive-system port-forward svc/toolhive-registry-server 28082:8080 >/tmp/re8ch-toolhive-registry-verify.log 2>&1 &
REGISTRY_PF_PID=$!
sleep 1
registry_names=$(curl -fsS http://127.0.0.1:28082/registry/approved/v0.1/servers | jq -r '[.servers[].server.name]')
[ "$(printf '%s' "$registry_names" | jq 'length')" -eq 4 ] || fail "Registry does not contain exactly four entries"
printf '%s' "$registry_names" | jq -e '
  index("com.toolhive.k8s.toolhive-system/ops-observe-managed") != null and
  index("com.toolhive.k8s.toolhive-system/ops-operate-managed") != null and
  index("com.toolhive.k8s.toolhive-system/ops-admin-managed") != null and
  index("com.toolhive.k8s.toolhive-system/ops-observe-external") != null
' >/dev/null || fail "Registry entry names mismatch"

kubectl --server="$KUBE_SERVER" -n toolhive-system port-forward svc/mcp-ops-observe-managed-proxy 28088:8080 >/tmp/re8ch-toolhive-observe-verify.log 2>&1 &
OBSERVE_PF_PID=$!
sleep 1
authorization=$(kubectl --server="$KUBE_SERVER" -n kagent get secret toolhive-ops-observe-token -o jsonpath='{.data.authorization}' | base64 -d)
health_ok=false
health='{}'
for _attempt in 1 2 3; do
  health=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    http://127.0.0.1:28088/mcp \
    -d '{"jsonrpc":"2.0","id":901,"method":"tools/call","params":{"name":"get_platform_service_health","arguments":{}}}' 2>/dev/null | \
    jq -r '.result.content[0].text' || printf '{}')
  if [ "$(printf '%s' "$health" | jq '.summary.reachable // 0')" -ge 10 ] && \
    printf '%s' "$health" | jq -e '
      [.services[] | select(.name == "harbor-registry" or .name == "cloudbeaver" or .name == "grafana" or .name == "headlamp" or .name == "toolhive-registry") | .status]
      | length == 5 and all(. == "healthy")
    ' >/dev/null; then
    health_ok=true
    break
  fi
  sleep 2
done
catalog=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":902,"method":"tools/call","params":{"name":"get_platform_service_catalog","arguments":{}}}' | \
  jq -r '.result.content[0].text')
permission_coverage=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":918,"method":"tools/call","params":{"name":"get_platform_permission_coverage","arguments":{}}}' | \
  jq -r '.result.content[0].text')
posture=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":903,"method":"tools/call","params":{"name":"get_platform_access_posture","arguments":{}}}' | \
  jq -r '.result.content[0].text')
operation_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":904,"method":"tools/call","params":{"name":"get_managed_platform_operation_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
nuclio_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":905,"method":"tools/call","params":{"name":"get_nuclio_function_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
nuclio_diagnostic=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":906,"method":"tools/call","params":{"name":"diagnose_nuclio_function","arguments":{"namespace":"qwen","name":"qianwen-prod-workflow-api"}}}' | \
  jq -r '.result.content[0].text')
nuclio_capacity=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":907,"method":"tools/call","params":{"name":"get_nuclio_capacity_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
litellm_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":908,"method":"tools/call","params":{"name":"get_litellm_gateway_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
fleet_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":909,"method":"tools/call","params":{"name":"get_fleet_control_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
supabase_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":910,"method":"tools/call","params":{"name":"get_supabase_platform_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
neo4j_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":911,"method":"tools/call","params":{"name":"get_neo4j_platform_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
identity_status='{}'
for _attempt in 1 2 3; do
  identity_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    http://127.0.0.1:28088/mcp \
    -d '{"jsonrpc":"2.0","id":912,"method":"tools/call","params":{"name":"get_identity_platform_status","arguments":{}}}' | \
    jq -r '.result.content[0].text')
  if printf '%s' "$identity_status" | jq -e '
    ([.workloads[] | select(.name == "re8ch-auth-gateway")][0] | .desired == 1 and .ready == 1) and
    ([.probes[] | select(.name == "re8ch-auth-gateway")][0].status == "healthy")
  ' >/dev/null; then
    break
  fi
  sleep 2
done
agent_runtime_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":913,"method":"tools/call","params":{"name":"get_agent_runtime_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
observability_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":914,"method":"tools/call","params":{"name":"get_observability_platform_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
artifact_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":915,"method":"tools/call","params":{"name":"get_artifact_platform_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
database_routing_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":916,"method":"tools/call","params":{"name":"get_database_routing_status","arguments":{}}}' | \
  jq -r '.result.content[0].text')
external_publication_status=$(curl -fsS -H "Authorization: $authorization" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28088/mcp \
  -d '{"jsonrpc":"2.0","id":917,"method":"tools/call","params":{"name":"get_external_publication_readiness","arguments":{}}}' | \
  jq -r '.result.content[0].text')
unset authorization
[ "$health_ok" = true ] || fail "core HTTP service domains did not all become healthy within three attempts"
printf '%s' "$supabase_status" | jq -e '
  .studio.status == "healthy" and .summary.workloads == 8 and
  (.summary.readyWorkloads + .summary.unreadyWorkloads) == 8 and
  .summary.unreadyWorkloads >= 1 and
  (.protectedNativeAPIs | index("PostgREST data/schema") != null) and
  (has("users") | not) and (has("rows") | not) and (has("keys") | not)
' >/dev/null || fail "Supabase platform status is incomplete, unexpectedly healthy, or exposes restricted data"
printf '%s' "$neo4j_status" | jq -e '
  .summary.instances == 3 and .summary.readyInstances >= 1 and .summary.unreadyInstances >= 1 and
  (.protectedNativeAPIs | index("Bolt/Cypher queries") != null) and
  (has("nodes") | not) and (has("relationships") | not) and (has("cypher") | not) and (has("credentials") | not)
' >/dev/null || fail "Neo4j platform status or native authorization boundary is invalid"
printf '%s' "$identity_status" | jq -e '
  .summary.workloads == 5 and
  ([.workloads[] | select(.name == "authentik-redis")][0] | .desired == 1 and .ready == 1) and
  ([.workloads[] | select(.name == "authentik-server")][0] | .desired == 1 and .ready == 1) and
  ([.workloads[] | select(.name == "authentik-worker")][0] | .desired == 1 and .ready == 1) and
  ([.probes[] | select(.name == "authentik")][0].status == "healthy") and
  ([.workloads[] | select(.name == "re8ch-auth-gateway")][0] | .desired == 1 and .ready == 1) and
  ([.probes[] | select(.name == "re8ch-auth-gateway")][0].status == "healthy") and
  (.protectedNativeAPIs | index("users/groups") != null) and
  (has("users") | not) and (has("groups") | not) and (has("tokens") | not) and (has("sessions") | not)
' >/dev/null || fail "identity platform status or native authorization boundary is invalid"
printf '%s' "$agent_runtime_status" | jq -e --argjson observeCount "$observe_tool_count" --argjson operateCount "$operate_tool_count" '
  .summary.workloads >= 15 and .summary.remoteMCPServers >= 4 and
  .summary.acceptedRemoteMCPServers == .summary.remoteMCPServers and
  (.remoteMCPServers | map(select(.name == "toolhive-ops-observe" and .discoveredTools == $observeCount)) | length) == 1 and
  (.remoteMCPServers | map(select(.name == "toolhive-ops-operate" and .discoveredTools == $operateCount)) | length) == 1 and
  (.protectedNativeAPIs | index("sandbox files/processes/commands/output") != null) and
  (has("prompts") | not) and (has("sessions") | not) and (has("credentials") | not) and (has("commands") | not)
' >/dev/null || fail "Agent runtime status or native authorization boundary is invalid"
printf '%s' "$observability_status" | jq -e '
  .summary.workloads == 19 and .summary.unreadyWorkloads >= 2 and
  .metricsTargets.totalTargets >= 100 and .metricsTargets.downTargets >= 1 and
  .clusterCostOneHour.available == true and (.clusterCostOneHour.totalEfficiency | type) == "number" and
  (.protectedNativeAPIs | index("arbitrary PromQL/LogsQL") != null) and
  (has("logs") | not) and (has("traces") | not) and (has("alerts") | not) and (has("series") | not) and (has("tenantCosts") | not)
' >/dev/null || fail "observability platform status or native authorization boundary is invalid"
printf '%s' "$artifact_status" | jq -e '
  .probe.status == "healthy" and .harborHealth.status == "healthy" and
  .summary.workloads == 12 and .summary.healthyHarborComponents == 7 and
  .summary.unreadyWorkloads >= 1 and .summary.scaledToZeroWorkloads >= 2 and
  (.protectedNativeAPIs | index("vulnerability findings") != null) and
  (has("robotSecrets") | not) and (has("credentials") | not) and (has("artifacts") | not) and (has("vulnerabilities") | not)
' >/dev/null || fail "artifact platform status or Harbor native authorization boundary is invalid"
printf '%s' "$database_routing_status" | jq -e '
  .summary.routes == 8 and .summary.reachableRoutes >= 1 and
  .summary.serviceRoutes == 3 and .summary.serviceRoutesWithReadyEndpoint == 3 and
  .cloudbeaver.status == "healthy" and
  (.protectedNativeAPIs | index("arbitrary SQL") != null) and
  (.endpointSlices | all(has("service") and has("dataRole") and has("readyEndpoints") and (has("addresses") | not))) and
  (has("passwords") | not) and (has("sql") | not) and (has("schemas") | not) and (has("rows") | not)
' >/dev/null || fail "database routing status or native authorization boundary is invalid"
printf '%s' "$external_publication_status" | jq -e '
  .gates.externalMCPServer == true and .gates.publicIngress == true and
  (has("clientSecret") | not) and (has("accessToken") | not) and (has("refreshToken") | not) and
  (has("dnsCredentials") | not) and (has("userClaims") | not)
' >/dev/null || fail "external publication readiness or credential redaction is invalid"
[ "$(printf '%s' "$catalog" | jq '.summary.namespaces')" -ge 35 ] || fail "platform catalog covers fewer than 35 active namespaces"
[ "$(printf '%s' "$catalog" | jq '.summary.explicitlyClassified')" -eq "$(printf '%s' "$catalog" | jq '.summary.namespaces')" ] || fail "one or more active namespaces lack explicit service-domain policy"
[ "$(printf '%s' "$catalog" | jq '.summary.defaultApplicationData')" -eq 0 ] || fail "platform catalog still relies on default namespace policy"
[ "$(printf '%s' "$catalog" | jq '.summary.nativeAuthorityMapped')" -eq "$(printf '%s' "$catalog" | jq '.summary.namespaces')" ] || fail "one or more namespaces lack native authority mapping"
[ "$(printf '%s' "$catalog" | jq '.summary.materializedPolicyMatched')" -eq "$(printf '%s' "$catalog" | jq '.summary.namespaces')" ] || fail "one or more namespace policy labels drift from the ToolHive classification"
[ "$(printf '%s' "$catalog" | jq '.summary.services')" -ge 100 ] || fail "platform catalog covers fewer than 100 Services"
printf '%s' "$catalog" | jq -e '
  [.namespaces[].namespace] as $names |
  ["auth", "harbor", "kagent", "llm-gateway", "neo4j", "opensandbox-system", "pigsty-external", "qwen", "supabase-pg-re8ch", "toolhive-system"] |
  all(. as $required | $names | index($required) != null)
' >/dev/null || fail "platform catalog is missing one or more required service domains"
printf '%s' "$catalog" | jq -e '
  [.namespaces[].workloads[]] | length >= 150 and
  all(has("desired") and has("ready") and has("available") and has("scaledToZero"))
' >/dev/null || fail "platform workload readiness catalog is incomplete"
printf '%s' "$catalog" | jq -e '
  .namespaces | all(.explicitPolicy == true and (.nativeAuthority | length) > 0 and
    (.agentPermissions.observe | length) > 0 and (.agentPermissions.operate | length) > 0 and
    (.agentPermissions.admin | length) > 0 and (.humanTracking | length) > 0)
' >/dev/null || fail "namespace access-policy entries are incomplete"
printf '%s' "$permission_coverage" | jq -e '
  .coverageComplete == true and
  .summary.namespaces >= 35 and .summary.workloads >= 150 and .summary.services >= 100 and
  .summary.resourceDecisions == (.summary.workloads + .summary.services) and
  .summary.managedAdminWorkloads == 9 and .summary.tenantOperateWorkloads >= 1 and
  .summary.unclassifiedNamespaces == 0 and .summary.policyLabelMismatches == 0 and
  .summary.namespacesMissingHumanTracking == 0 and
  .summary.namespacesMissingNativeAuthority == 0 and
  (.resources | all(
    has("resourceType") and has("namespace") and has("kind") and has("name") and
    has("domain") and has("plane") and has("aiAccess") and
    has("nativeAuthority") and has("humanTracking") and
    (.aiAccess | has("observe") and has("operate") and has("admin"))
  )) and
  (has("secrets") | not) and (has("environment") | not) and (has("podSpecs") | not)
' >/dev/null || fail "platform permission coverage is incomplete or exposes restricted configuration"
[ "$(printf '%s' "$posture" | jq '.summary.namespaces')" -ge 35 ] || fail "access posture covers fewer than 35 active namespaces"
printf '%s' "$posture" | jq -e '.namespaces | all(has("findings") and has("counts") and has("humanReview"))' >/dev/null || fail "access posture entries are incomplete"
[ "$(printf '%s' "$operation_status" | jq '.summary.total')" -eq 9 ] || fail "managed platform operation status does not cover nine targets"
printf '%s' "$operation_status" | jq -e '
  [.targets[] | select(.namespace == "nuclio" and .deployment == "nuclio-dashboard")][0] |
  .desired == 0 and (.lastScale.at | length) > 0 and (.lastScale.by | length) > 0
' >/dev/null || fail "managed platform operation audit tracking is missing"
[ "$(printf '%s' "$nuclio_status" | jq '.summary.total')" -ge 30 ] || fail "Nuclio status covers fewer than 30 functions"
printf '%s' "$nuclio_status" | jq -e '
  .functions | all((has("namespace") and has("name") and has("state") and has("image")) and
  (has("env") | not) and (has("annotations") | not))
' >/dev/null || fail "Nuclio status is incomplete or exposes restricted configuration"
printf '%s' "$nuclio_diagnostic" | jq -e '
  .namespace == "qwen" and .name == "qianwen-prod-workflow-api" and
  (.function | has("state") and has("image") and (has("env") | not)) and
  has("pods") and has("deployments") and has("recentEvents") and
  (has("annotations") | not)
' >/dev/null || fail "Nuclio diagnostic is incomplete or exposes restricted configuration"
[ "$(printf '%s' "$nuclio_capacity" | jq '.summary.nodes')" -ge 2 ] || fail "Nuclio capacity covers fewer than two labelled nodes"
printf '%s' "$nuclio_capacity" | jq -e '
  .nodes | all(has("ready") and has("unschedulable") and has("taints") and has("allocatable") and has("nuclioPods"))
' >/dev/null || fail "Nuclio capacity status is incomplete"
printf '%s' "$litellm_status" | jq -e '
  .readiness.status == "healthy" and .readiness.db == "connected" and
  .deployment.desired == 1 and .deployment.ready == 1 and
  (.protectedNativeAPIs | index("/model/info") != null) and
  (has("masterKey") | not) and (has("providerKeys") | not)
' >/dev/null || fail "LiteLLM safe readiness or native authorization boundary is invalid"
printf '%s' "$fleet_status" | jq -e '
  .health.status == "healthy" and (.version.version | length) > 0 and
  (.workloads | length) == 4 and ([.workloads[] | .desired == 1 and .ready == 1] | all) and
  (.protectedNativeAPIs | index("/api/v1/fleet/hosts") != null) and
  (has("apiToken") | not) and (has("hosts") | not)
' >/dev/null || fail "Fleet safe status or native authorization boundary is invalid"

kubectl --server="$KUBE_SERVER" -n toolhive-system port-forward svc/mcp-ops-admin-managed-proxy 28090:8080 >/tmp/re8ch-toolhive-admin-verify.log 2>&1 &
ADMIN_PF_PID=$!
sleep 1
admin_token=$(kubectl --server="$KUBE_SERVER" -n toolhive-system create token platform-admin-agent \
  --audience=re8ch-toolhive-ops-admin-managed --duration=10m)
admin_tools=$(curl -fsS -H "Authorization: Bearer $admin_token" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:28090/mcp \
  -d '{"jsonrpc":"2.0","id":904,"method":"tools/list","params":{}}' | jq '.result.tools')
unset admin_token
[ "$(printf '%s' "$admin_tools" | jq 'length')" -eq 38 ] || fail "admin tool count is not 38"
printf '%s' "$admin_tools" | jq -e '
  [.[].name] |
  index("restart_managed_platform_deployment") != null and
  index("scale_managed_platform_deployment") != null and
  index("set_tool_publication_stage") != null and
  index("get_platform_permission_coverage") != null and
  index("get_supabase_platform_status") != null and
  index("get_neo4j_platform_status") != null and
  index("get_identity_platform_status") != null and
  index("get_agent_runtime_status") != null and
  index("get_observability_platform_status") != null and
  index("get_artifact_platform_status") != null and
  index("get_database_routing_status") != null and
  index("get_external_publication_readiness") != null and
  index("k8s_api_proxy") == null and index("postgres_sql_proxy") == null
' >/dev/null || fail "admin allowlist mismatch"

kubectl --server="$KUBE_SERVER" -n qianwen-ops port-forward svc/panel-victoria-metrics 28428:8428 >/tmp/re8ch-toolhive-vm-verify.log 2>&1 &
VM_PF_PID=$!
sleep 1
up_count=0
for _attempt in 1 2 3; do
  up_count=$(curl -fsSG http://127.0.0.1:28428/api/v1/query \
    --data-urlencode 'query=count(up{job="toolhive-mcp-control-plane"} == 1)' | \
    jq -r '.data.result[0].value[1] // "0"')
  [ "$up_count" = 3 ] && break
  sleep 2
done
[ "$up_count" = 3 ] || fail "not all three ToolHive metrics targets are up"

coredns_desired=$(kubectl --server="$KUBE_SERVER" -n kube-system get deployment coredns -o jsonpath='{.spec.replicas}')
[ "$coredns_desired" -eq 2 ] || fail "CoreDNS desired replica count is not two"
coredns_ready=$(kubectl --server="$KUBE_SERVER" -n kube-system get pods -l k8s-app=kube-dns -o json | \
  jq '[.items[] | select(.status.phase == "Running") | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))] | length')
[ "$coredns_ready" -ge 2 ] || fail "CoreDNS does not have at least two Ready pods"

echo "PASS: managed ToolHive Ops surfaces, permissions, Registry, service health, telemetry, and CoreDNS"
