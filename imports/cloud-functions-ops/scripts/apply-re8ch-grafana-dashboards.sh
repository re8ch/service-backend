#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.67}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
GRAFANA_SELECTOR="${GRAFANA_SELECTOR:-app.kubernetes.io/name=panel-grafana}"
LOCAL_SOURCE_DIR="${LOCAL_SOURCE_DIR:-manifests/k3s/qianwen-ops/grafana-dashboards/qianwen}"
REMOTE_TARGET_DIR="${REMOTE_TARGET_DIR:-/var/lib/grafana/dashboards/qianwen}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

if [[ ! -d "$LOCAL_SOURCE_DIR" ]]; then
  echo "dashboard source dir not found: $LOCAL_SOURCE_DIR" >&2
  exit 1
fi

python3 - "$LOCAL_SOURCE_DIR" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.glob("*.json")):
    with path.open() as f:
        json.load(f)
print("re8ch dashboard json ok")
PY

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

cp "$LOCAL_SOURCE_DIR"/*.json "$tmpdir"/

remote_dir="/tmp/re8ch-dashboards.$$"
ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "rm -rf '$remote_dir' && mkdir -p '$remote_dir'"
scp "${SSH_OPTS[@]}" "$tmpdir"/*.json "$PIGSTY_HOST:$remote_dir/" >/dev/null

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
pod=\$(kubectl -n '$NAMESPACE' get pod -l '$GRAFANA_SELECTOR' -o jsonpath='{.items[0].metadata.name}')
kubectl -n '$NAMESPACE' exec \"\$pod\" -- mkdir -p '$REMOTE_TARGET_DIR'
kubectl -n '$NAMESPACE' exec \"\$pod\" -- sh -lc 'rm -f \"\$1\"/*.json \"\$1\"/*.json.bak-* \"\$1\"/*.bak-*' _ '$REMOTE_TARGET_DIR'
for file in '$remote_dir'/*.json; do
  kubectl -n '$NAMESPACE' cp \"\$file\" \"\$pod:$REMOTE_TARGET_DIR/\$(basename \"\$file\")\"
done
kubectl -n '$NAMESPACE' exec \"\$pod\" -- sh -lc 'chown -R 472:472 \"\$1\"' _ '$REMOTE_TARGET_DIR'
rm -rf '$remote_dir'
kubectl -n '$NAMESPACE' rollout restart deploy/panel-grafana
kubectl -n '$NAMESPACE' rollout status deploy/panel-grafana --timeout=180s
"

echo "re8ch grafana dashboards synced"
