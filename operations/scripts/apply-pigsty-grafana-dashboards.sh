#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@10.181.22.73}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
GRAFANA_SELECTOR="${GRAFANA_SELECTOR:-app.kubernetes.io/name=panel-grafana}"
REMOTE_SOURCE_DIR="${REMOTE_SOURCE_DIR:-/home/ubuntu/pigsty/files/grafana/pgsql}"
REMOTE_TARGET_DIR="${REMOTE_TARGET_DIR:-/var/lib/grafana/dashboards/pigsty}"
STATIC_PLUGIN_URL="${STATIC_PLUGIN_URL:-https://grafana.com/api/platform/plugins/marcusolsson-static-datasource/versions/latest/download}"
STATIC_PLUGIN_ID="${STATIC_PLUGIN_ID:-marcusolsson-static-datasource}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

local_tmpdir="$(mktemp -d)"
cleanup_local() {
  rm -rf "$local_tmpdir"
}
trap cleanup_local EXIT

curl -fL --retry 2 --connect-timeout 10 --max-time 60 -o "$local_tmpdir/$STATIC_PLUGIN_ID.zip" "$STATIC_PLUGIN_URL" >/dev/null
remote_plugin_zip="/tmp/$STATIC_PLUGIN_ID.$$.zip"
scp "${SSH_OPTS[@]}" "$local_tmpdir/$STATIC_PLUGIN_ID.zip" "$PIGSTY_HOST:$remote_plugin_zip" >/dev/null

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
tmpdir=\$(mktemp -d)
cleanup() {
  rm -rf \"\$tmpdir\"
  rm -f '$remote_plugin_zip'
}
trap cleanup EXIT

dashdir=\"\$tmpdir/dashboards\"
mkdir -p \"\$dashdir\"
cp '$REMOTE_SOURCE_DIR'/*.json \"\$dashdir\"/
python3 - \"\$dashdir\" <<'PY'
import json
import os
import sys

root = sys.argv[1]
patched = {}

def patch_datasources(value):
    changes = 0
    if isinstance(value, dict):
        datasource = value.get(\"datasource\")
        if isinstance(datasource, dict) and datasource.get(\"uid\") in {\"\$dsn\", \"\${dsn}\"}:
            if datasource.get(\"type\") == \"prometheus\":
                datasource[\"type\"] = \"postgres\"
                changes += 1
            if datasource.get(\"uid\") == \"\$dsn\":
                datasource[\"uid\"] = \"\${dsn}\"
                changes += 1
        for child in value.values():
            changes += patch_datasources(child)
    elif isinstance(value, list):
        for child in value:
            changes += patch_datasources(child)
    return changes

for name in sorted(os.listdir(root)):
    if not name.startswith(\"pgcat-\") or not name.endswith(\".json\"):
        continue
    path = os.path.join(root, name)
    with open(path) as f:
        dashboard = json.load(f)
    changes = patch_datasources(dashboard)
    if changes:
        with open(path, \"w\") as f:
            json.dump(dashboard, f, ensure_ascii=False, separators=(\",\", \":\"))
    patched[name] = changes

for name, changes in patched.items():
    print(f\"{name}: patched_datasources={changes}\")
PY

pod=\$(kubectl -n '$NAMESPACE' get pod -l '$GRAFANA_SELECTOR' -o jsonpath='{.items[0].metadata.name}')
plugin_tmp=\"\$tmpdir/plugin\"
python3 - '$remote_plugin_zip' \"\$plugin_tmp\" <<'PY'
from pathlib import Path
from zipfile import ZipFile
import sys

archive = Path(sys.argv[1])
target = Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)
with ZipFile(archive) as z:
    z.extractall(target)
PY
kubectl -n '$NAMESPACE' exec \"\$pod\" -- sh -lc 'mkdir -p /var/lib/grafana/plugins && rm -rf /var/lib/grafana/platform/plugins/'$STATIC_PLUGIN_ID''
kubectl -n '$NAMESPACE' cp \"\$plugin_tmp/$STATIC_PLUGIN_ID\" \"\$pod:/var/lib/grafana/platform/plugins/$STATIC_PLUGIN_ID\"
kubectl -n '$NAMESPACE' exec \"\$pod\" -- sh -lc 'chown -R 472:472 /var/lib/grafana/platform/plugins/'$STATIC_PLUGIN_ID''
kubectl -n '$NAMESPACE' exec \"\$pod\" -- mkdir -p '$REMOTE_TARGET_DIR'
kubectl -n '$NAMESPACE' exec \"\$pod\" -- rm -rf '$REMOTE_TARGET_DIR/plugin'
kubectl -n '$NAMESPACE' cp \"\$dashdir/.\" \"\$pod:$REMOTE_TARGET_DIR\"
kubectl -n '$NAMESPACE' exec \"\$pod\" -- sh -lc 'chown -R 472:472 \"\$1\"' _ '$REMOTE_TARGET_DIR'
kubectl -n '$NAMESPACE' rollout restart deploy/panel-grafana
kubectl -n '$NAMESPACE' rollout status deploy/panel-grafana --timeout=180s
"

echo "pigsty grafana dashboards synced"
