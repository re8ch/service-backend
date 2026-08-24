#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"

KUBECTL="$KUBECTL" "$base_dir/preflight.sh"
${KUBECTL} -n netbird-access get secret netbird-routing-peer-la >/dev/null
${KUBECTL} apply -f "$base_dir/routing-peers.yaml"
${KUBECTL} -n netbird-access rollout status deploy/netbird-router-la --timeout=5m
${KUBECTL} -n netbird-access exec deploy/netbird-router-la -c netbird -- netbird status
