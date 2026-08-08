#!/usr/bin/env bash
set -euo pipefail
: "${KUBECTL:=sudo k3s kubectl}"
: "${NAMESPACE:=egress-fabric}"

${KUBECTL} apply -f namespace.yaml
temp="$(mktemp -d)"
trap 'rm -rf -- "$temp"' EXIT
if ! ${KUBECTL} -n "$NAMESPACE" get secret egress-fabric-paths >/dev/null 2>&1; then
  printf '%s\n' 'proxies: []' > "$temp/paths.yaml"
  ${KUBECTL} -n "$NAMESPACE" create secret generic egress-fabric-paths \
    --from-file=paths.yaml="$temp/paths.yaml"
fi
echo "External-path Secret exists; its value was not printed."
