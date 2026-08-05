#!/usr/bin/env sh
set -eu

: "${KUBE_SERVER:=https://192.0.2.176:6443}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OPS_SOURCE="$SCRIPT_DIR/../ops/ops-mcp-server-app.py"

kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/../ops/coredns-availability-policy.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/../ops/local-path-provisioner-availability-policy.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/namespace-access-classification.yaml"
kubectl --server="$KUBE_SERVER" -n toolhive-system create configmap ops-mcp-server-code \
  --from-file=app.py="$OPS_SOURCE" \
  --dry-run=client -o yaml | kubectl --server="$KUBE_SERVER" apply -f -
kubectl --server="$KUBE_SERVER" -n ops get secret ops-mcp-harbor-auth -o json | \
  jq '.metadata = {name: "ops-mcp-harbor-auth", namespace: "toolhive-system"}' | \
  kubectl --server="$KUBE_SERVER" apply -f -
kubectl --server="$KUBE_SERVER" -n auth get secret toolhive-authentik-admin-api -o json | \
  jq '.metadata = {name: "toolhive-authentik-admin-api", namespace: "toolhive-system"}' | \
  kubectl --server="$KUBE_SERVER" apply -f -
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/proxy-scheduling-policy.yaml"
# Admission policies are observed asynchronously by kube-apiserver. Give the
# binding a short establishment window before the operator writes Deployments.
sleep 5
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/ops-mcp-publication.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/ops-mcp-managed-observe.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/ops-mcp-managed-privileged.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/platform-restart-rbac.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/external-observe-publication.yaml"
kubectl --server="$KUBE_SERVER" apply -f "$SCRIPT_DIR/external-privileged-publication.yaml"

# The reviewed Python source is projected from a ConfigMap. Restart the managed
# backends so a publication never reports a new tool schema while executing an
# older source projection.
for server in ops-observe-managed ops-operate-managed ops-admin-managed ops-observe-external ops-operate-external ops-admin-external; do
  old_uid=$(kubectl --server="$KUBE_SERVER" -n toolhive-system get pod "$server-0" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)
  kubectl --server="$KUBE_SERVER" -n toolhive-system delete pod "$server-0" --wait=false --ignore-not-found
  ready=false
  for _attempt in $(seq 1 180); do
    pod_state=$(kubectl --server="$KUBE_SERVER" -n toolhive-system get pod "$server-0" -o json 2>/dev/null | \
      jq -r '[.metadata.uid, (.metadata.deletionTimestamp // ""), ([.status.conditions[]? | select(.type == "Ready") | .status][0] // "False")] | @tsv' || true)
    new_uid=$(printf '%s' "$pod_state" | cut -f1)
    deleting=$(printf '%s' "$pod_state" | cut -f2)
    pod_ready=$(printf '%s' "$pod_state" | cut -f3)
    if [ -n "$new_uid" ] && [ "$new_uid" != "$old_uid" ] && [ -z "$deleting" ] && [ "$pod_ready" = "True" ]; then
      ready=true
      break
    fi
    sleep 1
  done
  if [ "$ready" != true ]; then
    echo "Timed out waiting for a fresh Ready backend pod: $server-0" >&2
    exit 1
  fi
done

# Proxy-runners cache the backend tool schema. Restart them only after the
# backends are current so MCPToolConfig filtering and client discovery agree.
for server in ops-observe-managed ops-operate-managed ops-admin-managed ops-observe-external ops-operate-external ops-admin-external; do
  kubectl --server="$KUBE_SERVER" -n toolhive-system rollout restart deployment/"$server"
done

for server in ops-observe-managed ops-operate-managed ops-admin-managed ops-observe-external ops-operate-external ops-admin-external; do
  kubectl --server="$KUBE_SERVER" -n toolhive-system rollout status deployment/"$server" --timeout=180s
done
