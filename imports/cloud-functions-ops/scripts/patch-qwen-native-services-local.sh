#!/usr/bin/env bash
set -euo pipefail

namespace="${QWEN_NAMESPACE:-qwen}"

services=(
  qianwen-region-metadata
  nuclio-qianwen-prod-analytics
  nuclio-qianwen-prod-api-core
  nuclio-qianwen-prod-integrations-api
  nuclio-qianwen-prod-oss-sts
  nuclio-qianwen-prod-tasks
  nuclio-qianwen-prod-assets-api
  nuclio-qianwen-prod-image-api
  nuclio-qianwen-prod-text-api
  nuclio-qianwen-prod-video-api
  nuclio-qianwen-prod-video-meta
  nuclio-qianwen-prod-reverse-image-prompt
  nuclio-qianwen-prod-script-api
  nuclio-qianwen-prod-storyboard-api
  nuclio-qianwen-prod-workflow-api
)

for svc in "${services[@]}"; do
  echo "checking endpoints for ${svc}" >&2
  endpoints="$(
    kubectl -n "${namespace}" get endpointslice \
      -l "kubernetes.io/service-name=${svc}" \
      -o jsonpath='{range .items[*].endpoints[*]}{.nodeName}{" "}{.conditions.ready}{"\n"}{end}' |
      sort |
      uniq
  )"
  printf '%s\n' "${endpoints}" >&2
  ready_nodes="$(printf '%s\n' "${endpoints}" | awk '$2 == "true" {print $1}' | sort -u | wc -l | tr -d ' ')"
  if [[ "${ready_nodes}" -lt "${QWEN_MIN_LOCAL_READY_NODES:-3}" ]]; then
    echo "skip ${svc}: only ${ready_nodes} ready node-local endpoints" >&2
    continue
  fi
  kubectl -n "${namespace}" patch svc "${svc}" --type merge \
    -p '{"spec":{"internalTrafficPolicy":"Local"}}' >/dev/null
  echo "patched ${svc} internalTrafficPolicy=Local"
done
