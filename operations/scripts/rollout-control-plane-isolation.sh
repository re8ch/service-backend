#!/usr/bin/env bash
set -euo pipefail

mode="${1:-observe}"
apply="${2:-}"
nodes=(restricted-node-296f36f2fb restricted-node-1eb0570f74 restricted-node-6dc3f140f9)
taint_key="node-role.kubernetes.io/control-plane"
system_namespaces='^(kube-system|flux-system|rook-ceph|scheduling-system|scheduler-system|observability-system|among-clusters|kyverno|kueue-system|cert-manager|opentelemetry-operator)$'

if [[ ! "$mode" =~ ^(observe|prefer|enforce|rollback)$ ]]; then
  echo "usage: $0 observe|prefer|enforce|rollback [--apply]" >&2
  exit 2
fi

kubectl get --raw='/readyz' >/dev/null
for node in "${nodes[@]}"; do
  [[ "$(kubectl get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" == "True" ]] || {
    echo "gate failed: $node is not Ready" >&2
    exit 1
  }
done

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
pending="$(kubectl get pods -A --field-selector=status.phase=Pending -o json | \
  python3 "$script_dir/control-plane-pending-gate.py" "${nodes[@]}")"
if [[ "$pending" != "0" && "$mode" != "observe" && "$mode" != "rollback" ]]; then
  echo "gate failed: $pending Pending pods can target control-plane nodes" >&2
  exit 1
fi

ordinary=0
for node in "${nodes[@]}"; do
  while read -r namespace name owner allowed; do
    [[ -z "$namespace" || "$namespace" =~ $system_namespaces || "$owner" == "DaemonSet" || "$allowed" == "true" ]] && continue
    printf '%s\t%s/%s\t%s\n' "$node" "$namespace" "$name" "$owner"
    ordinary=$((ordinary + 1))
  done < <(kubectl get pods -A --field-selector="spec.nodeName=$node,status.phase=Running" \
    -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].kind,ALLOWED:.metadata.labels.scheduling\.re8ch\.com/control-plane-allowed' --no-headers)
done

if [[ "$mode" == "observe" ]]; then
  echo "ordinary control-plane pods: $ordinary"
  kubectl get nodes "${nodes[@]}" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints'
  exit 0
fi

if [[ "$mode" == "enforce" && "$ordinary" != "0" ]]; then
  echo "gate failed: $ordinary ordinary pods remain on control-plane nodes" >&2
  exit 1
fi

dry_run=(--dry-run=server -o name)
[[ "$apply" == "--apply" ]] && dry_run=()

case "$mode" in
  prefer)
    kubectl taint nodes "${nodes[@]}" "${taint_key}=true:PreferNoSchedule" --overwrite "${dry_run[@]}"
    ;;
  enforce)
    kubectl taint nodes "${nodes[@]}" "${taint_key}:PreferNoSchedule-" "${dry_run[@]}" || true
    kubectl taint nodes "${nodes[@]}" "${taint_key}=true:NoSchedule" --overwrite "${dry_run[@]}"
    ;;
  rollback)
    kubectl taint nodes "${nodes[@]}" "${taint_key}:NoSchedule-" "${dry_run[@]}" || true
    kubectl taint nodes "${nodes[@]}" "${taint_key}:PreferNoSchedule-" "${dry_run[@]}" || true
    ;;
esac

kubectl get nodes "${nodes[@]}" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints'
