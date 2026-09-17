#!/usr/bin/env bash
set -euo pipefail

expected_nodes='restricted-node-1eb0570f74 restricted-node-296f36f2fb'
failed=0

check_workloads() {
  local namespace=$1 selector=$2
  while IFS=$'\t' read -r kind name affinity; do
    [[ -z "$kind" ]] && continue
    if [[ "$affinity" != *"restricted-node-1eb0570f74"* || "$affinity" != *"restricted-node-296f36f2fb"* ]]; then
      echo "FAIL ${namespace} ${kind}/${name}: A1/B1 node affinity missing" >&2
      failed=1
    fi
  done < <(kubectl -n "$namespace" get deployment,statefulset -l "$selector" \
    -o jsonpath='{range .items[*]}{.kind}{"\t"}{.metadata.name}{"\t"}{.spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms}{"\n"}{end}')

  declare -A workload_nodes=()
  while IFS=$'\t' read -r name workload node phase ready; do
    [[ -z "$name" ]] && continue
    if [[ " $expected_nodes " != *" $node "* || "$phase" == "Pending" || "$ready" != "True" ]]; then
      echo "FAIL ${namespace} pod/${name}: node=${node:-<missing>} phase=$phase ready=$ready" >&2
      failed=1
    fi
    workload_nodes["$workload"]+=" $node"
  done < <(kubectl -n "$namespace" get pod -l "$selector" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.toolhive-name}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}')

  for workload in "${!workload_nodes[@]}"; do
    nodes=" ${workload_nodes[$workload]} "
    if [[ "$nodes" != *" restricted-node-1eb0570f74 "* || "$nodes" != *" restricted-node-296f36f2fb "* ]]; then
      echo "FAIL ${namespace} ${workload}: replicas are not split across A1/B1 (${nodes})" >&2
      failed=1
    fi
  done
}

check_workloads toolhive-system 'toolhive=true'
check_workloads tenant-infrastructure 'ai.connectool.io/mcp-backend=true'

if (( failed )); then
  exit 1
fi
echo "All MCP workloads are Ready and split across A1/B1."
