#!/usr/bin/env sh
set -eu

: "${KUBE_SERVER:=https://192.0.2.18:6443}"
KUBECTL="kubectl --server=$KUBE_SERVER -n kagent"

attach_once() {
  agent_name=$1
  remote_name=$2
  tool_names=$3
  if $KUBECTL get agent "$agent_name" -o json | jq -e --arg remote "$remote_name" \
    '.spec.declarative.tools[]? | select(.mcpServer.name == $remote)' >/dev/null; then
    printf '%s already references %s\n' "$agent_name" "$remote_name"
    return
  fi
  patch=$(jq -cn --arg remote "$remote_name" --argjson tools "$tool_names" \
    '[{"op":"add","path":"/spec/declarative/tools/-","value":{"type":"McpServer","mcpServer":{"apiGroup":"kagent.dev","kind":"RemoteMCPServer","name":$remote,"namespace":"kagent","toolNames":$tools}}}]')
  $KUBECTL patch agent "$agent_name" --type=json -p "$patch"
}

move_to_healthy_node() {
  agent_name=$1
  desired_node=$2
  current_node=$($KUBECTL get agent "$agent_name" -o jsonpath='{.spec.declarative.deployment.nodeSelector.kubernetes\.io/hostname}')
  if [ "$current_node" = "$desired_node" ]; then
    return
  fi
  patch=$(jq -cn --arg node "$desired_node" \
    '[{"op":"add","path":"/spec/declarative/deployment/nodeSelector/kubernetes.io~1hostname","value":$node}]')
  $KUBECTL patch agent "$agent_name" --type=json -p "$patch"
}

attach_once readonly-k8s-diagnostic-agent toolhive-ops-observe \
  '["get_usage_guide","get_tenant_context","list_k8s_resources","get_registry_info","get_tenant_resource_catalog","get_grafana_links","get_tenant_metrics_summary"]'
attach_once my-first-k8s-agent toolhive-ops-operate \
  '["get_usage_guide","get_tenant_context","list_k8s_resources","get_registry_info","get_tenant_resource_catalog","get_grafana_links","get_tenant_metrics_summary","restart_deployment"]'

move_to_healthy_node readonly-k8s-diagnostic-agent restricted-node-296f36f2fb
move_to_healthy_node my-first-k8s-agent restricted-node-296f36f2fb
