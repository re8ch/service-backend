#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"

${KUBECTL} -n netbird-access get secret netbird-geodb-sync >/dev/null
${KUBECTL} -n netbird-access create configmap netbird-geodb-sync \
  --from-file=geodb-sync.py="$base_dir/geodb-sync.py" \
  --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} apply -f "$base_dir/geodb-sync.yaml"
job="netbird-geodb-sync-manual-$(date +%s)"
${KUBECTL} -n netbird-access create job --from=cronjob/netbird-geodb-sync "$job"
${KUBECTL} -n netbird-access wait --for=condition=complete "job/$job" --timeout=15m
${KUBECTL} -n netbird-access logs "job/$job"
