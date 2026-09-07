#!/usr/bin/env bash
set -euo pipefail

expected_node=restricted-node-6dc3f140f9
failed=0

check_workloads() {
  local namespace=$1 selector=$2
  while IFS=$'\t' read -r kind name selected; do
    [[ -z "$kind" ]] && continue
    if [[ "$selected" != "$expected_node" ]]; then
      echo "FAIL ${namespace} ${kind}/${name}: selector=${selected:-<missing>}" >&2
      failed=1
    fi
  done < <(kubectl -n "$namespace" get deployment,statefulset -l "$selector" \
    -o jsonpath='{range .items[*]}{.kind}{"\t"}{.metadata.name}{"\t"}{.spec.template.spec.nodeSelector.kubernetes\.io/hostname}{"\n"}{end}')

  while IFS=$'\t' read -r name node phase ready; do
    [[ -z "$name" ]] && continue
    if [[ "$node" != "$expected_node" || "$phase" == "Pending" || "$ready" != "True" ]]; then
      echo "FAIL ${namespace} pod/${name}: node=${node:-<missing>} phase=$phase ready=$ready" >&2
      failed=1
    fi
  done < <(kubectl -n "$namespace" get pod -l "$selector" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}')
}

check_workloads toolhive-system 'toolhive=true'
check_workloads tenant-infrastructure 'ai.connectool.io/mcp-backend=true'

if (( failed )); then
  exit 1
fi
echo "All MCP workloads are Ready on ${expected_node}."
