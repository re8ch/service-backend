#!/usr/bin/env bash
set -euo pipefail

mode="${1:-observe}"
apply="${2:-}"
nodes=(restricted-node-296f36f2fb restricted-node-1eb0570f74 restricted-node-6dc3f140f9)
taint_key="node-role.kubernetes.io/control-plane"

if [[ ! "$mode" =~ ^(observe|prefer|enforce|rollback)$ ]]; then
  echo "usage: $0 observe|prefer|enforce|rollback [--apply]" >&2
  exit 2
fi

# A single successful request can hide one unhealthy API/etcd member behind
# the service VIP. Require consecutive successes before changing node state.
for _ in 1 2 3; do
  kubectl get --raw='/readyz' >/dev/null
  sleep 2
done
for node in "${nodes[@]}"; do
  [[ "$(kubectl get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" == "True" ]] || {
    echo "gate failed: $node is not Ready" >&2
    exit 1
  }
done

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
pending="$(kubectl get pods -A --field-selector=status.phase=Pending -o json | \
  python3 "$script_dir/control-plane-pending-gate.py" "${nodes[@]}")"
# PreferNoSchedule is intentionally the non-disruptive first stage: it does not
# reject a pod when no alternative exists and never evicts an assigned pod.
# Keep the hard Pending-pod gate for the NoSchedule enforcement stage only.
if [[ "$pending" != "0" && "$mode" == "enforce" ]]; then
  echo "gate failed: $pending Pending pods can target control-plane nodes" >&2
  exit 1
fi

inventory="$($script_dir/control-plane-workload-inventory.py "${nodes[@]}")"
printf '%s\n' "$inventory" | awk -F '\t' '$4 == "managed-conflict" || $4 == "unmanaged"'
ordinary="$(printf '%s\n' "$inventory" | awk -F '\t' '$4 == "managed-conflict" || $4 == "unmanaged" {count++} END {print count+0}')"
managed="$(printf '%s\n' "$inventory" | awk -F '\t' '$4 == "managed-conflict" {count++} END {print count+0}')"
unmanaged="$(printf '%s\n' "$inventory" | awk -F '\t' '$4 == "unmanaged" {count++} END {print count+0}')"

if [[ "$mode" == "observe" ]]; then
  echo "control-plane placement conflicts: $ordinary (managed=$managed unmanaged=$unmanaged)"
  kubectl get nodes "${nodes[@]}" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints'
  exit 0
fi

if [[ "$mode" == "enforce" && "$ordinary" != "0" ]]; then
  echo "gate failed: $ordinary owner-resolved placement conflicts remain (managed=$managed unmanaged=$unmanaged)" >&2
  exit 1
fi

taint_nodes() {
  if [[ "$apply" == "--apply" ]]; then
    kubectl taint nodes "${nodes[@]}" "$@"
  else
    kubectl taint nodes "${nodes[@]}" "$@" --dry-run=server -o name
  fi
}

case "$mode" in
  prefer)
    taint_nodes "${taint_key}=true:PreferNoSchedule" --overwrite
    ;;
  enforce)
    taint_nodes "${taint_key}:PreferNoSchedule-" || true
    taint_nodes "${taint_key}=true:NoSchedule" --overwrite
    ;;
  rollback)
    taint_nodes "${taint_key}:NoSchedule-" || true
    taint_nodes "${taint_key}:PreferNoSchedule-" || true
    ;;
esac

kubectl get nodes "${nodes[@]}" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints'
