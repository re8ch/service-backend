#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.192}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
MANIFEST="${MANIFEST:-manifests/k3s/qianwen-ops/panel-observability.yaml}"
PG_MONITOR_SECRET="${PG_MONITOR_SECRET:-panel-grafana-pg-monitor}"
GRAFANA_DEPLOYMENT="${GRAFANA_DEPLOYMENT:-panel-grafana}"
VM_DEPLOYMENT="${VM_DEPLOYMENT:-panel-victoria-metrics}"
RE8CH_VMALERT_DEPLOYMENT="${RE8CH_VMALERT_DEPLOYMENT:-panel-re8ch-vmalert}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

if [[ ! -f "$MANIFEST" ]]; then
  echo "manifest not found: $MANIFEST" >&2
  exit 1
fi

python3 - "$MANIFEST" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as f:
    list(yaml.safe_load_all(f))
print("manifest yaml ok")
PY

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" 'python3 -' >"$tmpdir/pg_monitor_password" <<'PY'
from pathlib import Path

pgpass = Path("/home/ubuntu/.pgpass")
if pgpass.exists():
    for line in pgpass.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) >= 5 and parts[3] == "dbuser_monitor" and parts[4]:
            print(parts[4])
            raise SystemExit

for line in Path("/home/ubuntu/pigsty/pigsty.yml").read_text().splitlines():
    text = line.strip()
    if text.startswith("pg_monitor_password:"):
        value = text.split(":", 1)[1].strip().strip("\"'")
        if not value:
            raise SystemExit("pg_monitor_password is empty")
        print(value)
        break
else:
    raise SystemExit("pg_monitor_password not found in /home/ubuntu/pigsty/pigsty.yml")
PY

if [[ ! -s "$tmpdir/pg_monitor_password" ]]; then
  echo "failed to read pg_monitor_password" >&2
  exit 1
fi
python3 - "$tmpdir/pg_monitor_password" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(path.read_text().rstrip("\r\n"))
PY

remote_secret="/tmp/${PG_MONITOR_SECRET}.$$"
scp "${SSH_OPTS[@]}" "$tmpdir/pg_monitor_password" "$PIGSTY_HOST:$remote_secret" >/dev/null

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
chmod 0600 '$remote_secret'
kubectl -n '$NAMESPACE' create secret generic '$PG_MONITOR_SECRET' \
  --from-file=password='$remote_secret' \
  --dry-run=client -o yaml | kubectl apply -f -
rm -f '$remote_secret'
"

scp "${SSH_OPTS[@]}" "$MANIFEST" "$PIGSTY_HOST:/tmp/panel-observability.yaml" >/dev/null
ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
kubectl apply -f /tmp/panel-observability.yaml
kubectl -n '$NAMESPACE' rollout restart deploy/'$VM_DEPLOYMENT'
kubectl -n '$NAMESPACE' rollout restart deploy/'$GRAFANA_DEPLOYMENT'
kubectl -n '$NAMESPACE' rollout restart deploy/'$RE8CH_VMALERT_DEPLOYMENT'
kubectl -n '$NAMESPACE' rollout status deploy/'$VM_DEPLOYMENT' --timeout=180s
kubectl -n '$NAMESPACE' rollout status deploy/'$GRAFANA_DEPLOYMENT' --timeout=180s
kubectl -n '$NAMESPACE' rollout status deploy/'$RE8CH_VMALERT_DEPLOYMENT' --timeout=180s
"

PIGSTY_HOST="$PIGSTY_HOST" NAMESPACE="$NAMESPACE" ./scripts/apply-pigsty-vmalert-rules.sh
PIGSTY_HOST="$PIGSTY_HOST" NAMESPACE="$NAMESPACE" ./scripts/apply-pigsty-grafana-dashboards.sh

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
kubectl -n '$NAMESPACE' wait --for=condition=Available \
  deploy/'$GRAFANA_DEPLOYMENT' deploy/'$VM_DEPLOYMENT' deploy/panel-pigsty-vmalert deploy/'$RE8CH_VMALERT_DEPLOYMENT' \
  --timeout=180s
kubectl -n '$NAMESPACE' get deploy panel-grafana panel-victoria-metrics panel-pigsty-vmalert panel-re8ch-vmalert \
  -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,AVAILABLE:.status.availableReplicas,IMAGE:.spec.template.spec.containers[0].image
kubectl -n '$NAMESPACE' get secret '$PG_MONITOR_SECRET' -o jsonpath='{.data.password}' | grep -q . && echo PG_MONITOR_SECRET_PRESENT
kubectl -n '$NAMESPACE' get deploy '$GRAFANA_DEPLOYMENT' \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name==\"PG_MONITOR_PASSWORD\")].valueFrom.secretKeyRef.name}' |
  grep -qx '$PG_MONITOR_SECRET' && echo GRAFANA_USES_PG_MONITOR_SECRET
vm_cluster_ip=\$(kubectl -n '$NAMESPACE' get svc panel-victoria-metrics -o jsonpath='{.spec.clusterIP}')
python3 - \"\$vm_cluster_ip\" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

vm_url = f\"http://{sys.argv[1]}:8428\"
query = urllib.parse.quote(\"count(pg_up)\", safe=\"\")
with urllib.request.urlopen(f\"{vm_url}/api/v1/query?query={query}\", timeout=10) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False))
PY
"

echo "panel observability apply complete"
