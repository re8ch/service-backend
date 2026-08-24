#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"
MANIFEST="${MANIFEST:-$ROOT/clusters/re8ch/components/qianwen-ops/observable-collector.yaml}"
COLLECTOR="${COLLECTOR:-$ROOT/operations/scripts/observable_collector.py}"

kctl() {
  # shellcheck disable=SC2086
  $KUBECTL_CMD "$@"
}

python3 -m py_compile "$COLLECTOR"

kctl -n "$NAMESPACE" create configmap observable-collector-code \
  --from-file=observable_collector.py="$COLLECTOR" \
  --dry-run=client -o yaml | kctl apply -f -

kctl apply -f "$MANIFEST"
kctl -n "$NAMESPACE" rollout status deploy/observable-collector --timeout=180s
kctl -n "$NAMESPACE" get deploy observable-collector
