#!/usr/bin/env python3
"""Low-frequency PGCAT snapshot collector for Observable.

The collector does not open PostgreSQL wire-protocol connections itself. It
executes a small allowlist of read-only SQL through existing Grafana PostgreSQL
datasources, then writes tenant-scoped snapshots through Observable PostgREST.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from typing import Any


DEFAULT_RW_BASE = os.getenv("OBSERVABLE_RPC_RW_BASE_URL", "https://db-rw.re8ch.com/observable").rstrip("/")
GRAFANA_URL = os.getenv("GRAFANA_URL", "https://grafana.re8ch.com/grafana").rstrip("/")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", "")
GRAFANA_BASIC_AUTH = os.getenv("GRAFANA_BASIC_AUTH", "")
JWT_SECRET = os.getenv("PGRST_JWT_SECRET", "")
JWT_ROLE = os.getenv("OBSERVABLE_SERVICE_ROLE", "observable_app_rw")
JWT_AUD = os.getenv("PGRST_JWT_AUD", DEFAULT_RW_BASE)
JWT_ISS = os.getenv("OBSERVABLE_JWT_ISS", "observable-pgcat-collector")
INTERVAL_SECONDS = int(os.getenv("OBSERVABLE_PGCAT_COLLECT_INTERVAL_SECONDS", "300"))
REQUEST_TIMEOUT = int(os.getenv("OBSERVABLE_REQUEST_TIMEOUT_SECONDS", "30"))
TARGETS_JSON = os.getenv("OBSERVABLE_PGCAT_TARGETS", "")
TARGETS_FILE = os.getenv("OBSERVABLE_PGCAT_TARGETS_FILE", "")


SQL_ALLOWLIST = {
    "database": """
select
  datname,
  numbackends,
  xact_commit,
  xact_rollback,
  blks_read,
  blks_hit,
  tup_returned,
  tup_fetched,
  tup_inserted,
  tup_updated,
  tup_deleted,
  temp_bytes,
  deadlocks,
  pg_database_size(datname) as size_bytes
from pg_stat_database
where datname = current_database()
""",
    "locks": """
select
  mode,
  granted,
  count(*)::bigint as lock_count
from pg_locks
group by mode, granted
order by lock_count desc, mode
limit 64
""",
    "schema": """
select
  schemaname,
  count(*)::bigint as table_count,
  sum(n_live_tup)::bigint as live_tuples,
  sum(n_dead_tup)::bigint as dead_tuples,
  sum(vacuum_count + autovacuum_count)::bigint as vacuum_count,
  sum(analyze_count + autoanalyze_count)::bigint as analyze_count
from pg_stat_user_tables
group by schemaname
order by table_count desc, schemaname
limit 128
""",
    "table": """
select
  schemaname,
  relname,
  n_live_tup,
  n_dead_tup,
  seq_scan,
  idx_scan,
  vacuum_count + autovacuum_count as vacuum_count,
  analyze_count + autoanalyze_count as analyze_count,
  pg_total_relation_size(format('%I.%I', schemaname, relname)) as total_bytes
from pg_stat_user_tables
order by pg_total_relation_size(format('%I.%I', schemaname, relname)) desc
limit 100
""",
    "query": """
select
  queryid::text as queryid,
  calls,
  total_exec_time,
  mean_exec_time,
  rows,
  shared_blks_hit,
  shared_blks_read,
  shared_blks_dirtied,
  shared_blks_written,
  wal_bytes
from pg_stat_statements
where dbid = (select oid from pg_database where datname = current_database())
order by total_exec_time desc
limit 50
""",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(level: str, message: str, **fields: Any) -> None:
    payload = {"ts": now_iso(), "level": level, "message": message}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def service_jwt() -> str:
    if not JWT_SECRET:
        raise RuntimeError("PGRST_JWT_SECRET is required")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "role": JWT_ROLE,
        "service": True,
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = "%s.%s" % (
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
    )
    sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + b64url(sig)


def http_json(method: str, url: str, body: Any | None = None, headers: dict[str, str] | None = None) -> Any:
    data = None
    out_headers = {"Accept": "application/json"}
    if headers:
        out_headers.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        out_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=out_headers)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def grafana_headers() -> dict[str, str]:
    if GRAFANA_TOKEN:
        return {"Authorization": "Bearer " + GRAFANA_TOKEN}
    if GRAFANA_BASIC_AUTH:
        return {"Authorization": "Basic " + GRAFANA_BASIC_AUTH}
    raise RuntimeError("GRAFANA_TOKEN or GRAFANA_BASIC_AUTH is required")


def load_targets() -> list[dict[str, Any]]:
    raw = TARGETS_JSON
    if TARGETS_FILE:
        with open(TARGETS_FILE, "r", encoding="utf-8") as handle:
            raw = handle.read()
    if not raw.strip():
        return []
    targets = json.loads(raw)
    if not isinstance(targets, list):
        raise RuntimeError("OBSERVABLE_PGCAT_TARGETS must be a JSON array")
    return targets


def grafana_query(datasource_uid: str, sql: str) -> dict[str, Any]:
    payload = {
        "from": "now-5m",
        "to": "now",
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": datasource_uid},
                "format": "table",
                "rawSql": sql,
            }
        ],
    }
    return http_json("POST", GRAFANA_URL + "/api/ds/query", payload, headers=grafana_headers())


def frame_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    frames = (((result.get("results") or {}).get("A") or {}).get("frames") or [])
    rows: list[dict[str, Any]] = []
    for frame in frames:
        fields = frame.get("schema", {}).get("fields", [])
        values = (frame.get("data") or {}).get("values") or []
        names = [field.get("name") or "" for field in fields]
        if not names or not values:
            continue
        for row_values in zip(*values):
            rows.append({name: value for name, value in zip(names, row_values) if name})
    return rows


def numeric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"row_count": len(rows)}
    if len(rows) == 1:
        for key, value in rows[0].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                summary[key] = value
    return summary


def collect_target(target: dict[str, Any]) -> list[dict[str, Any]]:
    tenant_id = str(target.get("tenant_id") or target.get("tenant") or "").strip()
    database_name = str(target.get("database_name") or target.get("database") or "").strip()
    datasource_uid = str(target.get("datasource_uid") or target.get("datasource") or "").strip()
    instance_name = str(target.get("instance_name") or target.get("instance") or datasource_uid).strip()
    kinds = target.get("snapshot_kinds") or list(SQL_ALLOWLIST.keys())
    if not tenant_id or not database_name or not datasource_uid:
        raise RuntimeError("target requires tenant_id, database_name, and datasource_uid")

    observed_at = now_iso()
    snapshots: list[dict[str, Any]] = []
    for kind in kinds:
        if kind not in SQL_ALLOWLIST:
            raise RuntimeError("snapshot kind is not allowlisted: %s" % kind)
        try:
            result = grafana_query(datasource_uid, SQL_ALLOWLIST[kind])
            rows = frame_rows(result)
            metrics = numeric_summary(rows)
            raw_payload: dict[str, Any] = {"rows": rows}
        except Exception as exc:
            log(
                "warning",
                "pgcat snapshot kind failed",
                tenant_id=tenant_id,
                database_name=database_name,
                datasource_uid=datasource_uid,
                snapshot_kind=kind,
                error=str(exc),
                errorType=type(exc).__name__,
            )
            metrics = {"row_count": 0, "error": 1}
            raw_payload = {"error": str(exc), "errorType": type(exc).__name__}
        snapshots.append({
            "tenant_id": tenant_id,
            "database_name": database_name,
            "instance_name": instance_name,
            "source": "pgcat",
            "snapshot_kind": kind,
            "object_ref": "%s/%s/%s" % (instance_name, database_name, kind),
            "observed_at": observed_at,
            "metrics": metrics,
            "raw_payload": raw_payload,
        })
    return snapshots


def ingest(snapshots: list[dict[str, Any]], rw_base: str = DEFAULT_RW_BASE) -> Any:
    if not snapshots:
        return {"ok": True, "count": 0}
    url = rw_base.rstrip("/") + "/rpc/service_observable_pgcat_snapshot_ingest"
    return http_json("POST", url, {"p_snapshots": snapshots}, headers={"Authorization": "Bearer " + service_jwt()})


def run_once(dry_run: bool = False) -> int:
    targets = load_targets()
    snapshots: list[dict[str, Any]] = []
    for target in targets:
        try:
            snapshots.extend(collect_target(target))
        except Exception as exc:
            log("error", "pgcat target collection failed", target=target, error=str(exc), errorType=type(exc).__name__)
    if dry_run:
        print(json.dumps({"count": len(snapshots), "snapshots": snapshots}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    result = ingest(snapshots)
    log("info", "observable pgcat collection complete", collected=len(snapshots), result=result)
    return 0


def main() -> int:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    while True:
        try:
            run_once(dry_run=dry_run)
        except Exception as exc:
            log("error", "observable pgcat collection failed", error=str(exc), errorType=type(exc).__name__)
        if once:
            return 0
        time.sleep(max(INTERVAL_SECONDS, 60))


if __name__ == "__main__":
    raise SystemExit(main())
