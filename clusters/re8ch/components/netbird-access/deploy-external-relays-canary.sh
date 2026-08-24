#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"

# A regional relay rollout is node-scoped. An unrelated NotReady worker must
# not prevent another failure domain from gaining a relay; only the API/etcd,
# exact target nodes and existing public products are hard gates here.
ready="$(${KUBECTL} --request-timeout=15s get --raw=/readyz?verbose)"
grep -q '^\[+\]etcd ok' <<<"$ready"
grep -q '^\[+\]etcd-readiness ok' <<<"$ready"
for node in rancher-panel; do
  status="$(${KUBECTL} get node "$node" -o jsonpath='{range .status.conditions[?(@.type=="Ready")]}{.status}{end}')"
  [[ "$status" == True ]] || { echo "Target relay node $node is not Ready" >&2; exit 1; }
done
curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 https://access.re8ch.com:8443/ | grep -q '^200$'
curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 https://api-guangzhou24.re8ch.com/ | grep -Eq '^(200|401|403|404)$'
${KUBECTL} -n llm-gateway get secret re8ch-wildcard-tls -o json \
  | python3 "$base_dir/copy-tls-secret.py" \
  | ${KUBECTL} apply -f -
${KUBECTL} -n netbird-access get secret netbird-external-relay-auth >/dev/null
${KUBECTL} apply -f "$base_dir/external-relays-canary.yaml"
${KUBECTL} -n netbird-access rollout status deploy/netbird-relay-gz-1 --timeout=5m
if ! ${KUBECTL} -n netbird-access rollout status deploy/netbird-relay-hz-1 --timeout=30s; then
  echo "Optional relay netbird-relay-hz-1 did not converge; keeping it as a client-probed candidate." >&2
fi
