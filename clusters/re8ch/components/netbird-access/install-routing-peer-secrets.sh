#!/usr/bin/env bash
set -euo pipefail

: "${NETBIRD_NAMESPACE:=netbird-access}"
: "${KUBECTL:=sudo k3s kubectl}"
: "${NETBIRD_LA_SETUP_KEY:?Set NETBIRD_LA_SETUP_KEY from the NetBird dashboard}"

regions=(la)
if [[ -n "${NETBIRD_CN_SETUP_KEY:-}" ]]; then
  regions+=(cn)
fi

for region in "${regions[@]}"; do
  var="NETBIRD_${region^^}_SETUP_KEY"
  value="${!var}"
  ${KUBECTL} -n "$NETBIRD_NAMESPACE" create secret generic "netbird-routing-peer-${region}" \
    --from-literal=NB_SETUP_KEY="$value" \
    --dry-run=client -o yaml | ${KUBECTL} apply -f -
done

echo "Routing-peer setup keys installed. Clear NETBIRD_CN_SETUP_KEY and NETBIRD_LA_SETUP_KEY from the shell."
