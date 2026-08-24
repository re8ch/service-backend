#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"
snapshot="$base_dir/../egress-fabric/geodb/geodb.json.gz"
artifact_sha256="$(sha256sum "$snapshot" | awk '{print $1}')"
source_revision="$(python3 -c 'import gzip,json,sys; print(json.load(gzip.open(sys.argv[1], "rt"))["version"])' "$snapshot")"

${KUBECTL} -n netbird-access get secret netbird-geodb-sync >/dev/null
${KUBECTL} -n netbird-access create configmap netbird-geodb-sync \
  --from-file=geodb-sync.py="$base_dir/geodb-sync.py" \
  --dry-run=client -o yaml | ${KUBECTL} apply -f -
${KUBECTL} -n netbird-access create configmap re8ch-geodb-snapshot \
  --from-file=geodb.json.gz="$snapshot" \
  --dry-run=client -o yaml | ${KUBECTL} apply --server-side \
  --field-manager=re8ch-geodb-publisher -f -
${KUBECTL} -n netbird-access annotate configmap re8ch-geodb-snapshot --overwrite \
  "re8ch.com/artifact-sha256=$artifact_sha256" \
  "re8ch.com/source-revision=$source_revision"
${KUBECTL} apply -f "$base_dir/geodb-sync.yaml"
job="netbird-geodb-sync-manual-$(date +%s)"
${KUBECTL} -n netbird-access create job --from=cronjob/netbird-geodb-sync "$job"
${KUBECTL} -n netbird-access wait --for=condition=complete "job/$job" --timeout=15m
${KUBECTL} -n netbird-access logs "job/$job"
