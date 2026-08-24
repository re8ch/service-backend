#!/usr/bin/env bash
set -euo pipefail

failures=0
check() { if "$@"; then printf 'PASS: %s\n' "$*"; else printf 'FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); fi; }

check kubectl get node r640
check kubectl wait --for=condition=Ready node/r640 --timeout=30s
check kubectl -n lease get resourcequota akash-sandbox-hard-cap
check kubectl -n lease get limitrange akash-sandbox-container-limits
check kubectl get mutatingwebhookconfiguration akash-lease-admission

wrong_node_pods="$(kubectl -n lease get pods -o json | jq '[.items[] | select(.spec.nodeName != null and .spec.nodeName != "r640")] | length')"
if [[ "$wrong_node_pods" == "0" ]]; then printf 'PASS: all scheduled lease pods are on r640\n'; else printf 'FAIL: %s lease pods are on other nodes\n' "$wrong_node_pods" >&2; failures=$((failures + 1)); fi

unsafe="$({ printf '%s\n' 'apiVersion: v1' 'kind: Pod' 'metadata: {name: akash-admission-negative, namespace: lease}' 'spec:' '  hostNetwork: true' '  containers:' '    - {name: pause, image: registry.k8s.io/pause:3.10}'; } | kubectl apply --dry-run=server -f - 2>&1 || true)"
if grep -q 'hostNetwork is forbidden' <<<"$unsafe"; then printf 'PASS: admission rejects hostNetwork\n'; else printf 'FAIL: admission did not prove hostNetwork rejection: %s\n' "$unsafe" >&2; failures=$((failures + 1)); fi

if kubectl -n akash-gateway get gateway akash-gateway >/dev/null 2>&1; then
  check kubectl -n akash-gateway wait --for=condition=Programmed gateway/akash-gateway --timeout=30s
fi

if [[ "${VERIFY_PUBLIC:-false}" == "true" ]]; then
  check curl -fsS --max-time 10 http://provider-sandbox.akash.re8ch.com/
  check curl -fkSs --max-time 10 https://provider-sandbox.akash.re8ch.com/
  check curl -fkSs --max-time 10 https://provider-sandbox.akash.re8ch.com:8443/status
  check openssl s_client -connect provider-sandbox.akash.re8ch.com:8444 -servername provider-sandbox.akash.re8ch.com -brief </dev/null
fi

if [[ -n "${QWEN_SMOKE_URL:-}" ]]; then check curl -fsS --max-time 10 "$QWEN_SMOKE_URL"; fi
printf 'Verification failures: %d\n' "$failures"
(( failures == 0 ))
