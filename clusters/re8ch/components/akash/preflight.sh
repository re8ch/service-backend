#!/usr/bin/env bash
set -euo pipefail

failures=0
warnings=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
  warnings=$((warnings + 1))
}

pass() {
  printf 'PASS: %s\n' "$*"
}

command -v kubectl >/dev/null 2>&1 || {
  printf 'FAIL: kubectl is required\n' >&2
  exit 1
}

context="$(kubectl config current-context 2>/dev/null || true)"
[[ -n "$context" ]] || {
  printf 'FAIL: no current Kubernetes context\n' >&2
  exit 1
}
printf 'INFO: context=%s\n' "$context"

nodes_json="$(kubectl get nodes -o json)" || {
  printf 'FAIL: cannot read nodes with the current context\n' >&2
  exit 1
}

if command -v jq >/dev/null 2>&1; then
  node_count="$(jq '.items | length' <<<"$nodes_json")"
  not_ready="$(jq '[.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status != "True"))] | length' <<<"$nodes_json")"
  non_amd64="$(jq '[.items[] | select(.status.nodeInfo.architecture != "amd64")] | length' <<<"$nodes_json")"
  k3s_nodes="$(jq '[.items[] | select(.status.nodeInfo.kubeletVersion | contains("k3s"))] | length' <<<"$nodes_json")"
  re8ch_nodes="$(jq '[.items[] | select(any(.metadata.labels | keys[]; startswith("re8ch.com/") or startswith("pigsty.cc/") or startswith("qianwen.cc/")))] | length' <<<"$nodes_json")"
  cpu_total="$(jq '[.items[].status.allocatable.cpu | if test("m$") then (sub("m$"; "") | tonumber) / 1000 else tonumber end] | add | floor' <<<"$nodes_json")"
  mem_bytes="$(jq '[.items[].status.allocatable.memory | capture("(?<n>[0-9]+)(?<u>Ki|Mi|Gi)") | (.n | tonumber) * (if .u == "Ki" then 1024 elif .u == "Mi" then 1048576 else 1073741824 end)] | add' <<<"$nodes_json")"
else
  printf 'FAIL: jq is required for deterministic capacity checks\n' >&2
  exit 1
fi

if (( node_count > 0 )); then pass "$node_count node(s) discovered"; else fail "cluster has no nodes"; fi
if (( not_ready == 0 )); then pass "all nodes Ready"; else fail "$not_ready node(s) are not Ready"; fi
if (( non_amd64 == 0 )); then pass "all nodes are amd64"; else fail "$non_amd64 node(s) are not amd64"; fi

if (( re8ch_nodes > 0 )); then
  warn "detected $re8ch_nodes RE8CH/shared node(s); guarded shared-cluster mode requires admission and quota controls"
else
  pass "no RE8CH workload labels detected"
fi

if (( k3s_nodes > 0 )); then
  if [[ "${ALLOW_UNSUPPORTED_K3S:-false}" == "true" ]]; then
    warn "K3s is not the upstream supported provider baseline; lab override accepted"
  else
    fail "K3s detected; use upstream Kubespray, or set ALLOW_UNSUPPORTED_K3S=true for an isolated lab only"
  fi
else
  pass "K3s not detected"
fi

if (( cpu_total >= 8 )); then pass "allocatable CPU ${cpu_total} >= 8"; else fail "allocatable CPU ${cpu_total} < 8"; fi
min_mem_bytes=$((16 * 1024 * 1024 * 1024))
if (( mem_bytes >= min_mem_bytes )); then
  pass "allocatable memory meets 16 GiB lab minimum"
else
  fail "allocatable memory is below 16 GiB lab minimum"
fi

if kubectl api-resources --api-group gateway.networking.k8s.io | grep -q GatewayClass; then
  pass "Gateway API is available"
else
  fail "Gateway API/GatewayClass is unavailable"
fi

for node_port in 30080 30443 31443 31444; do
  if kubectl get services --all-namespaces -o json | jq -e --argjson port "$node_port" \
    '.items[] | select(any(.spec.ports[]?; .nodePort == $port))' >/dev/null; then
    fail "NodePort $node_port is already allocated"
  else
    pass "NodePort $node_port is free"
  fi
done

if kubectl auth can-i create customresourcedefinitions.apiextensions.k8s.io 2>/dev/null | grep -qx yes; then
  pass "current identity can create CRDs"
else
  warn "current identity cannot create CRDs; installation will need a reviewed cluster-admin path"
fi

printf 'INFO: failures=%d warnings=%d\n' "$failures" "$warnings"
(( failures == 0 ))
