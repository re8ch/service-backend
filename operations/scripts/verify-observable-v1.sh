#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SQL="$ROOT/sql/011_observable_events.sql"
PGCAT_SQL="$ROOT/sql/013_observable_pgcat_snapshots.sql"
MANIFEST="$ROOT/clusters/re8ch/components/qianwen-ops/observable-collector.yaml"
PGCAT_MANIFEST="$ROOT/clusters/re8ch/components/qianwen-ops/observable-pgcat-collector.yaml"
COLLECTOR="$ROOT/operations/scripts/observable_collector.py"
PGCAT_COLLECTOR="$ROOT/operations/scripts/observable_pgcat_collector.py"
KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"

python3 -m py_compile "$COLLECTOR" "$PGCAT_COLLECTOR"

for required in \
  "create table if not exists api.observable_events" \
  "create or replace function api.service_observable_event_ingest" \
  "alter table api.observable_events enable row level security" \
  "create policy observable_events_tenant_select" \
  "tenant_id <> 'platform'"
do
  if ! grep -Fq "$required" "$SQL"; then
    echo "missing SQL guardrail: $required" >&2
    exit 1
  fi
done

for required in \
  "create table if not exists api.observable_pgcat_snapshots" \
  "create or replace view api.observable_pgcat_latest_rows" \
  "create or replace function api.service_observable_pgcat_snapshot_ingest" \
  "alter table api.observable_pgcat_snapshots enable row level security" \
  "create policy observable_pgcat_snapshots_tenant_select" \
  "tenant_id <> 'platform'"
do
  if ! grep -Fq "$required" "$PGCAT_SQL"; then
    echo "missing PGCAT SQL guardrail: $required" >&2
    exit 1
  fi
done

for required in \
  "GRAFANA_URL" \
  "SQL_ALLOWLIST" \
  "/api/ds/query" \
  "service_observable_pgcat_snapshot_ingest"
do
  if ! grep -Fq "$required" "$PGCAT_COLLECTOR"; then
    echo "missing PGCAT collector guardrail: $required" >&2
    exit 1
  fi
done

if ! grep -Fq "name: observable-collector" "$MANIFEST"; then
  echo "observable collector deployment missing" >&2
  exit 1
fi
if ! grep -Fq "kubernetes.io/hostname: rancher-panel" "$MANIFEST"; then
  echo "observable collector must be pinned to rancher-panel" >&2
  exit 1
fi
if grep -Eq 'verbs: \[.*(create|update|patch|delete)' "$MANIFEST"; then
  echo "observable collector RBAC must stay read-only" >&2
  exit 1
fi
if grep -Eq 'nuclio|registry\.re8ch\.com/.*/processor' "$MANIFEST"; then
  echo "observable collector must not be modeled as a Nuclio workload" >&2
  exit 1
fi
if ! grep -Fq "OBSERVABLE_TENANT_ALIASES" "$MANIFEST"; then
  echo "observable collector missing tenant alias configuration" >&2
  exit 1
fi
if ! grep -Fq "name: observable-pgcat-collector" "$PGCAT_MANIFEST"; then
  echo "observable PGCAT collector CronJob missing" >&2
  exit 1
fi
if ! grep -Fq "kubernetes.io/hostname: rancher-panel" "$PGCAT_MANIFEST"; then
  echo "observable PGCAT collector must be pinned to rancher-panel" >&2
  exit 1
fi
if ! grep -Fq "GRAFANA_ORG_ID" "$PGCAT_MANIFEST"; then
  echo "observable PGCAT collector must query the platform Grafana org" >&2
  exit 1
fi

echo "observable_static_scope=ok"

if [[ "${LIVE:-0}" != "1" ]]; then
  exit 0
fi

kctl() {
  # shellcheck disable=SC2086
  $KUBECTL_CMD "$@"
}

kctl -n "$NAMESPACE" get deploy observable-collector >/dev/null
kctl auth can-i list events --as="system:serviceaccount:${NAMESPACE}:observable-collector" >/dev/null
if kctl auth can-i patch deployments -n qwen --as="system:serviceaccount:${NAMESPACE}:observable-collector" | grep -q yes; then
  echo "observable collector can mutate deployments; expected no" >&2
  exit 1
fi

pod_node="$(kctl -n "$NAMESPACE" get pod -l app.kubernetes.io/name=observable-collector -o jsonpath='{.items[0].spec.nodeName}')"
if [[ "$pod_node" != "rancher-panel" ]]; then
  echo "observable collector is on $pod_node, expected rancher-panel" >&2
  exit 1
fi

echo "observable_live_scope=ok"
