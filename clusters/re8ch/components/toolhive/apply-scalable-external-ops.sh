#!/usr/bin/env sh
set -eu

# Deploy only the external Ops MCP execution plane and its ToolHive vMCP edge.
# Existing OAuth/Redis Secrets must already be provisioned; this script never
# copies or prints Secret values and deliberately has no Qwen dependencies.
: "${KUBECTL:=kubectl}"
: "${OPS_NODE:=restricted-node-6dc3f140f9}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OPS_SOURCE="$SCRIPT_DIR/../ops/ops-mcp-server-app.py"

ready=$($KUBECTL get node "$OPS_NODE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
[ "$ready" = "True" ] || { echo "Ops node is not Ready: $OPS_NODE" >&2; exit 1; }
$KUBECTL get --raw=/readyz >/dev/null
$KUBECTL -n toolhive-system wait --for=condition=Ready pod/toolhive-auth-redis-0 --timeout=60s

$KUBECTL -n toolhive-system create configmap ops-mcp-server-code \
  --from-file=app.py="$OPS_SOURCE" --dry-run=client -o yaml | $KUBECTL apply -f -
$KUBECTL apply -f "$SCRIPT_DIR/proxy-scheduling-policy.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/external-observe-publication.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/external-privileged-publication.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/vmcp-external-publication.yaml"

for workload in ops-observe-external ops-operate-external ops-admin-external; do
  $KUBECTL -n toolhive-system rollout status "deployment/$workload" --timeout=180s
done
for workload in re8ch-admin-vmcp re8ch-tools-vmcp; do
  $KUBECTL -n toolhive-system rollout status "deployment/$workload" --timeout=180s
done

$KUBECTL -n toolhive-system get \
  mcpservers.toolhive.stacklok.dev,virtualmcpservers.toolhive.stacklok.dev,mcpserverentries.toolhive.stacklok.dev
