#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
forbidden='(user_id|userId|session_id|sessionId|feature|workflow|project_id|projectId|asset_type|assetType|billing_plan|billingPlan)'
targets=(
  "$ROOT/manifests/k3s/qianwen-ops"
  "$ROOT/scripts/apply-panel-observability.sh"
  "$ROOT/scripts/apply-pigsty-vmalert-rules.sh"
  "$ROOT/scripts/verify-panel-observability.sh"
)

matches="$(rg -n "$forbidden" "${targets[@]}" || true)"
if [[ -n "$matches" ]]; then
  echo "Grafana/VM scope contains business semantic labels:" >&2
  echo "$matches" >&2
  exit 1
fi

echo "observability_scope=ok"
