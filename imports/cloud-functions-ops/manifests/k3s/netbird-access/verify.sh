#!/usr/bin/env bash
set -euo pipefail

: "${KUBECTL:=sudo k3s kubectl}"

KUBECTL="$KUBECTL" "$(dirname "$0")/preflight.sh"
${KUBECTL} -n netbird-access get deploy,pod,svc,ingressroute,pvc
${KUBECTL} -n netbird-access get pods -o wide
${KUBECTL} -n netbird-access exec deploy/netbird-router-cn -c netbird -- netbird status --json
${KUBECTL} -n netbird-access exec deploy/netbird-router-la -c netbird -- netbird status --json

for path in / /oauth2/.well-known/openid-configuration; do
  code="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 "https://access.re8ch.com:8443${path}")"
  printf '%-44s %s\n' "$path" "$code"
done

echo "Verify UDP 3478 separately from an external host after the edge DNAT is installed."
