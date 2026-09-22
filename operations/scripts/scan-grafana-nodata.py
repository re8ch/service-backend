#!/usr/bin/env python3
"""Scan Grafana dashboards for non-log panels that return no data.

The scanner is intentionally read-only. It uses Grafana's query API so the
result matches the tenant org datasource configuration instead of querying
VictoriaMetrics or PostgreSQL directly.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOG_DATASOURCE_TYPES = {
    "loki",
    "victoriametrics-logs-datasource",
}
LOG_DATASOURCE_UIDS = {
    "tenant-vlogs",
    "re8ch-vlogs",
    "re8ch-loki",
}


def kubectl(args: list[str]) -> str:
    cmd = os.environ.get("KUBECTL_CMD", "kubectl").split() + args
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def secret_key(namespace: str, secret: str, key: str) -> str:
    raw = kubectl(["-n", namespace, "get", "secret", secret, "-o", "jsonpath={.data.%s}" % key])
    return base64.b64decode(raw).decode()


class Grafana:
    def __init__(self, base_url: str, auth_header: str, org_id: int):
        self.base_url = base_url.rstrip("/")
        self.auth_header = auth_header
        self.org_id = org_id

    def request(self, method: str, path: str, body: Any | None = None, timeout: int = 30) -> Any:
        headers = {
            "Authorization": self.auth_header,
            "Accept": "application/json",
            "X-Grafana-Org-Id": str(self.org_id),
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw
            raise RuntimeError("HTTP %s %s: %s" % (exc.code, path, payload))


def auth_header(args: argparse.Namespace) -> str:
    if args.admin_token:
        return "Bearer " + args.admin_token
    username = args.admin_user or secret_key(args.namespace, args.admin_secret, "username")
    password = args.admin_password or secret_key(args.namespace, args.admin_secret, "password")
    raw = ("%s:%s" % (username, password)).encode()
    return "Basic " + base64.b64encode(raw).decode()


def datasource_uid(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("uid") or "")
    if isinstance(value, str):
        return value
    return ""


def datasource_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("type") or "")
    return ""


def is_log_datasource(value: Any) -> bool:
    uid = datasource_uid(value)
    kind = datasource_type(value)
    return uid in LOG_DATASOURCE_UIDS or kind in LOG_DATASOURCE_TYPES or "logs" in uid or "logs" in kind


def panel_path(panel: dict[str, Any], parents: list[str]) -> str:
    title = str(panel.get("title") or "panel-%s" % panel.get("id", "unknown"))
    return "/".join(parents + [title])


def walk_panels(panels: list[dict[str, Any]], parents: list[str] | None = None):
    parents = parents or []
    for panel in panels:
        children = panel.get("panels") or []
        if children:
            yield from walk_panels(children, parents + [str(panel.get("title") or "row")])
        else:
            yield panel, panel_path(panel, parents)


def variable_values(dashboard: dict[str, Any]) -> dict[str, str]:
    values = {
        "__interval": "30s",
        "__rate_interval": "1m",
    }
    for var in (dashboard.get("templating") or {}).get("list") or []:
        name = var.get("name")
        if not name:
            continue
        current = var.get("current") or {}
        value = current.get("value")
        if value in ("$__all", ["$__all"]) and var.get("allValue"):
            value = var.get("allValue")
        if isinstance(value, list):
            value = "|".join(str(item) for item in value)
        if value in (None, ""):
            value = current.get("text")
        if value in (None, ""):
            continue
        if isinstance(value, list):
            value = "|".join(str(item) for item in value)
        values[str(name)] = str(value)
    return values


def interpolate_string(value: str, variables: dict[str, str]) -> str:
    def replace_braced(match: re.Match[str]) -> str:
        return variables.get(match.group(1), match.group(0))

    value = re.sub(r"\$\{([A-Za-z0-9_]+)(?::[^}]*)?\}", replace_braced, value)
    for name, replacement in sorted(variables.items(), key=lambda item: len(item[0]), reverse=True):
        value = value.replace("$" + name, replacement)
    return value


def interpolate(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return interpolate_string(value, variables)
    if isinstance(value, list):
        return [interpolate(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: interpolate(child, variables) for key, child in value.items()}
    return value


def result_has_data(result: dict[str, Any]) -> bool:
    for frame in result.get("frames") or []:
        values = ((frame.get("data") or {}).get("values") or [])
        if any(isinstance(column, list) and len(column) > 0 for column in values):
            return True
    return False


def categorize(record: dict[str, str]) -> str:
    text = " ".join([
        record["dashboard"],
        record["dashboard_uid"],
        record["panel_path"],
        record["datasource_uids"],
        record["datasource_types"],
    ]).lower()
    if "pgcat" in text:
        return "PGCAT"
    if "pgbouncer" in text or "pool" in text:
        return "PgBouncer/pool"
    if "node" in text or "disk" in text or "network" in text or "filesystem" in text:
        return "Node/disk/network"
    if "pitr" in text or "backup" in text or "pgbackrest" in text:
        return "PITR/backup"
    if "replication" in text or "repl" in text or "subscription" in text:
        return "Replication"
    if "session" in text or "activity" in text:
        return "Session/activity"
    if "wal" in text or "checkpoint" in text or "archiver" in text:
        return "WAL/log/checkpoint"
    if "etcd" in text or "patroni" in text or "dcs" in text:
        return "DCS/etcd/Patroni"
    if "service" in text or "proxy" in text:
        return "Service/proxy"
    if "query" in text or "pg_stat_statement" in text:
        return "Query/pg_stat_statements"
    if "table" in text or "index" in text or "relname" in text:
        return "Table/index stats"
    return "Other"


def scan_dashboard(grafana: Grafana, dashboard: dict[str, Any], from_ms: int, to_ms: int) -> list[dict[str, str]]:
    variables = variable_values(dashboard)
    records: list[dict[str, str]] = []
    for panel, path in walk_panels(dashboard.get("panels") or []):
        panel_ds = panel.get("datasource")
        targets = panel.get("targets") or []
        if not targets:
            continue
        queries = []
        log_refs = []
        other_refs = []
        for target in targets:
            ref_id = str(target.get("refId") or "?")
            target_ds = target.get("datasource") or panel_ds
            if is_log_datasource(target_ds):
                log_refs.append(ref_id)
                continue
            if target.get("hide"):
                other_refs.append(ref_id)
                continue
            if not datasource_uid(target_ds):
                other_refs.append(ref_id)
                continue
            patched = interpolate(json.loads(json.dumps(target)), variables)
            patched["datasource"] = interpolate(target_ds, variables)
            patched.setdefault("intervalMs", 30000)
            patched.setdefault("maxDataPoints", 600)
            queries.append(patched)
        if not queries:
            continue
        body = {
            "from": str(from_ms),
            "to": str(to_ms),
            "queries": queries,
        }
        record = {
            "status": "",
            "category": "",
            "dashboard": str(dashboard.get("title") or ""),
            "dashboard_uid": str(dashboard.get("uid") or ""),
            "panel_path": path,
            "panel_id": str(panel.get("id") or ""),
            "datasource_uids": "|".join(sorted({datasource_uid(q.get("datasource")) for q in queries})),
            "datasource_types": "|".join(sorted({datasource_type(q.get("datasource")) for q in queries})),
            "target_refs": "|".join(str(q.get("refId") or "?") for q in queries),
            "reason": "",
            "window": "last_%sm" % int((to_ms - from_ms) / 60000),
        }
        try:
            payload = grafana.request("POST", "/api/ds/query", body, timeout=25)
            results = payload.get("results") or {}
            errors = []
            any_data = False
            for query in queries:
                ref_id = str(query.get("refId") or "?")
                result = results.get(ref_id) or {}
                if result.get("error"):
                    errors.append("%s %s" % (ref_id, result.get("error")))
                if result_has_data(result):
                    any_data = True
            if errors:
                record["status"] = "error"
                record["reason"] = "; ".join(errors)
            elif not any_data:
                record["status"] = "empty"
                record["reason"] = "all non-log query frames are empty"
            else:
                continue
        except Exception as exc:
            record["status"] = "error"
            record["reason"] = str(exc)
        record["category"] = categorize(record)
        records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Grafana tenant dashboards for no-data panels.")
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "https://grafana.service.re8ch.com/grafana"))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "qianwen-ops"))
    parser.add_argument("--admin-secret", default=os.environ.get("ADMIN_SECRET", "panel-grafana-admin-recovery"))
    parser.add_argument("--admin-user", default=os.environ.get("GRAFANA_ADMIN_USER", ""))
    parser.add_argument("--admin-password", default=os.environ.get("GRAFANA_ADMIN_PASSWORD", ""))
    parser.add_argument("--admin-token", default=os.environ.get("GRAFANA_ADMIN_TOKEN", ""))
    parser.add_argument("--org-id", type=int, default=int(os.environ.get("GRAFANA_TENANT_ORG_ID", "2")))
    parser.add_argument("--query", default="qianwen")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()

    grafana = Grafana(args.grafana_url, auth_header(args), args.org_id)
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - args.minutes * 60 * 1000
    search = grafana.request("GET", "/api/search?type=dash-db&query=%s" % urllib.parse.quote(args.query, safe=""))
    dashboards = [
        item for item in search
        if str(item.get("title") or "").startswith("qianwen - ")
        or str(item.get("title") or "").startswith("Tenant - qianwen")
    ]

    records: list[dict[str, str]] = []
    for index, item in enumerate(dashboards, 1):
        uid = item["uid"]
        payload = grafana.request("GET", "/api/dashboards/uid/%s" % urllib.parse.quote(uid, safe=""))
        dashboard = payload["dashboard"]
        print("scan=%s/%s uid=%s title=%s" % (index, len(dashboards), uid, dashboard.get("title")), flush=True)
        records.extend(scan_dashboard(grafana, dashboard, from_ms, now_ms))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    panels_path = args.out_dir / ("grafana-qianwen-nodata-panels-%s.csv" % suffix)
    summary_path = args.out_dir / ("grafana-qianwen-nodata-summary-%s.csv" % suffix)
    json_path = args.out_dir / ("grafana-qianwen-nodata-scan-%s.json" % suffix)

    fields = [
        "status", "category", "dashboard", "dashboard_uid", "panel_path", "panel_id",
        "datasource_uids", "datasource_types", "target_refs", "reason", "window",
    ]
    with panels_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    summary: dict[tuple[str, str], int] = {}
    for record in records:
        key = (record["status"], record["category"])
        summary[key] = summary.get(key, 0) + 1
    with summary_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "category", "panel_count"])
        writer.writeheader()
        for (status, category), count in sorted(summary.items()):
            writer.writerow({"status": status, "category": category, "panel_count": count})

    json_path.write_text(json.dumps({
        "window": "last_%sm" % args.minutes,
        "dashboards": len(dashboards),
        "records": records,
        "panels_csv": str(panels_path),
        "summary_csv": str(summary_path),
    }, ensure_ascii=False, indent=2))
    print("records=%d panels=%s summary=%s json=%s" % (len(records), panels_path, summary_path, json_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
