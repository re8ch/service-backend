#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"

KUBECTL="$KUBECTL" "$base_dir/preflight.sh"
KUBECTL="$KUBECTL" "$base_dir/bootstrap-control-plane.sh"

${KUBECTL} apply -f "$base_dir/control-plane.yaml"
${KUBECTL} apply -f "$base_dir/ingress.yaml"
${KUBECTL} apply -f "$base_dir/network-policy.yaml"
${KUBECTL} -n netbird-access rollout status deploy/netbird-server --timeout=5m
${KUBECTL} -n netbird-access rollout status deploy/netbird-dashboard --timeout=5m

curl -fsS --retry 12 --retry-delay 5 --max-time 10 \
  https://access.re8ch.com:8443/oauth2/.well-known/openid-configuration >/dev/null
echo "Control plane is reachable. Create the first owner at https://access.re8ch.com:8443/."
