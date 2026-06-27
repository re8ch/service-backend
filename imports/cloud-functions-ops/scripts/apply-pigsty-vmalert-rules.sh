#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.192}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
CONFIGMAP="${CONFIGMAP:-panel-pigsty-vmalert-rules}"
DEPLOYMENT="${DEPLOYMENT:-panel-pigsty-vmalert}"
SUPPLEMENTAL_RULES="${SUPPLEMENTAL_RULES:-manifests/k3s/qianwen-ops/pigsty-vmalert-supplemental.yml}"
SUPPLEMENTAL_RULE_FILES="${SUPPLEMENTAL_RULE_FILES:-$SUPPLEMENTAL_RULES manifests/k3s/qianwen-ops/pigsty-tenant-recording-rules.yml}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

read -r -a supplemental_files <<<"$SUPPLEMENTAL_RULE_FILES"
for rules_file in "${supplemental_files[@]}"; do
  if [[ ! -f "$rules_file" ]]; then
    echo "supplemental rules not found: $rules_file" >&2
    exit 1
  fi
done

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" 'timeout 45s cat /data/infra/rules/pgsql.yml' >"$tmpdir/pgsql.yml"

python3 - "$tmpdir/pgsql.yml" "$tmpdir/pgsql.patched.yml" "$tmpdir/supplemental.yml" "${supplemental_files[@]}" <<'PY'
from pathlib import Path
import sys
import yaml

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
supplemental_dst = Path(sys.argv[3])
supplemental_paths = [Path(path) for path in sys.argv[4:]]

merged = {"groups": []}
for supplemental_raw in supplemental_paths:
    supplemental = supplemental_raw.read_text()
    start = supplemental.find("groups:")
    if start < 0:
        raise SystemExit(f"could not find groups: in supplemental rules: {supplemental_raw}")
    supplemental = supplemental[start:]
    loaded = yaml.safe_load(supplemental)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("groups"), list):
        raise SystemExit(f"supplemental rules must contain a groups list: {supplemental_raw}")
    merged["groups"].extend(loaded["groups"])
supplemental_dst.write_text(yaml.safe_dump(merged, sort_keys=False))

rules = src.read_text()

pairs = [
    ("pgbouncer:db:xact_time_rate1m", "pgbouncer:db:xact_rate1m"),
    ("pgbouncer:ins:xact_time_rate1m", "pgbouncer:ins:xact_rate1m"),
    ("pgbouncer:cls:xact_time_rate1m", "pgbouncer:cls:xact_rate1m"),
    ("pgbouncer:env:xact_time_rate1m", "pgbouncer:env:xact_rate1m"),
    ("pgbouncer:db:xact_time_rate5m", "pgbouncer:db:xact_rate5m"),
    ("pgbouncer:ins:xact_time_rate5m", "pgbouncer:ins:xact_rate5m"),
    ("pgbouncer:cls:xact_time_rate5m", "pgbouncer:cls:xact_rate5m"),
    ("pgbouncer:env:xact_time_rate5m", "pgbouncer:env:xact_rate5m"),
    ("pgbouncer:db:xact_time_rate15m", "pgbouncer:db:xact_rate15m"),
    ("pgbouncer:ins:xact_time_rate15m", "pgbouncer:ins:xact_rate15m"),
    ("pgbouncer:cls:xact_time_rate15m", "pgbouncer:cls:xact_rate15m"),
    ("pgbouncer:env:xact_time_rate15m", "pgbouncer:env:xact_rate15m"),
    ("pgbouncer:db:query_time_rate1m", "pgbouncer:db:query_rate1m"),
    ("pgbouncer:ins:query_time_rate1m", "pgbouncer:ins:query_rate1m"),
    ("pgbouncer:cls:query_time_rate1m", "pgbouncer:cls:query_rate1m"),
    ("pgbouncer:env:query_time_rate1m", "pgbouncer:env:query_rate1m"),
    ("pgbouncer:db:query_time_rate5m", "pgbouncer:db:query_rate5m"),
    ("pgbouncer:ins:query_time_rate5m", "pgbouncer:ins:query_rate5m"),
    ("pgbouncer:cls:query_time_rate5m", "pgbouncer:cls:query_rate5m"),
    ("pgbouncer:env:query_time_rate5m", "pgbouncer:env:query_rate5m"),
    ("pgbouncer:db:query_time_rate15m", "pgbouncer:db:query_rate15m"),
    ("pgbouncer:ins:query_time_rate15m", "pgbouncer:ins:query_rate15m"),
    ("pgbouncer:cls:query_time_rate15m", "pgbouncer:cls:query_rate15m"),
    ("pgbouncer:env:query_time_rate15m", "pgbouncer:env:query_rate15m"),
]

changed = 0
for numerator, denominator in pairs:
    old = f"{numerator} / {denominator}"
    old_compact = f"{numerator}/ {denominator}"
    new = f"({numerator} / {denominator}) or (0 * {denominator})"
    if old in rules:
        rules = rules.replace(old, new, 1)
        changed += 1
    elif old_compact in rules:
        rules = rules.replace(old_compact, new, 1)
        changed += 1
    else:
        raise SystemExit(f"missing expected pgbouncer RT expression: {old}")

if changed != len(pairs):
    raise SystemExit(f"patched {changed} expressions, expected {len(pairs)}")

yaml.safe_load(rules)
dst.write_text(rules)
print(f"patched {changed} pgbouncer RT expressions")
PY

scp "${SSH_OPTS[@]}" "$tmpdir/pgsql.patched.yml" "$tmpdir/supplemental.yml" "$PIGSTY_HOST:/tmp/"

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "
set -euo pipefail
kubectl -n '$NAMESPACE' create configmap '$CONFIGMAP' \
  --from-file=agent.yml=/data/infra/rules/agent.yml \
  --from-file=node.yml=/data/infra/rules/node.yml \
  --from-file=pgsql.yml=/tmp/pgsql.patched.yml \
  --from-file=supplemental.yml=/tmp/supplemental.yml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n '$NAMESPACE' rollout restart deploy/'$DEPLOYMENT'
kubectl -n '$NAMESPACE' rollout status deploy/'$DEPLOYMENT' --timeout=180s
"
