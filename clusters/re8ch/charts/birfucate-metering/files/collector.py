#!/usr/bin/env python3
"""Prometheus-backed Occupancy and Bifurcation usage collector."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.getenv("BIRFUCATE_CONFIG", "/etc/birfucate/config.json"))
OUTBOX_PATH = Path(os.getenv("BIRFUCATE_OUTBOX", "/var/lib/birfucate/outbox.db"))
USAGE_TOKEN = os.getenv("BIRFUCATE_USAGE_TOKEN", "")
LOCK = threading.Lock()
METRICS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
READY = False
LAST_SUCCESS = 0.0
BROWSER_METRICS = frozenset({
    "birfucate_occupancy_current",
    "birfucate_occupancy_units_total",
    "birfucate_bifurcation_occurrences_total",
    "birfucate_bifurcation_intensity",
    "birfucate_bifurcation_fanout",
    "birfucate_bifurcation_score_total",
})


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def prometheus_query(base_url: str, query: str, timeout: int) -> list[dict[str, Any]]:
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus query failed")
    return payload.get("data", {}).get("result", [])


def browser_query(config: dict[str, Any], metric: str) -> dict[str, Any]:
    browser = config.get("browserAPI") or {}
    if not browser.get("enabled"):
        raise PermissionError("browser_api_disabled")
    if metric not in BROWSER_METRICS:
        raise ValueError("metric_not_allowed")
    lookback = max(60, min(3600, int(browser.get("lookbackSeconds", 600))))
    prom = config["prometheus"]
    result = prometheus_query(
        prom["baseURL"], f"last_over_time({metric}[{lookback}s])", int(prom["timeoutSeconds"])
    )
    return {"status": "success", "data": {"resultType": "vector", "result": result}}


def vector(items: list[dict[str, Any]], tenant_label: str, resource_label: str) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for item in items:
        labels = item.get("metric") or {}
        tenant, resource = str(labels.get(tenant_label) or ""), str(labels.get(resource_label) or "")
        if not tenant or not resource:
            continue
        try:
            value = float((item.get("value") or [0, 0])[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(value) and value >= 0:
            result[(tenant, resource)] = value
    return result


def set_metric(name: str, labels: dict[str, str], value: float) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with LOCK:
        METRICS[key] = float(value)


def add_metric(name: str, labels: dict[str, str], value: float) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with LOCK:
        METRICS[key] = METRICS.get(key, 0.0) + float(value)


def event_id(parts: list[Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    return "ue_" + hashlib.sha256(raw).hexdigest()[:32]


class Outbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,event_json TEXT NOT NULL,created_at INTEGER NOT NULL,delivered_at INTEGER)")

    def connect(self):
        return sqlite3.connect(self.path)

    def put(self, event: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,NULL)", (event["eventId"], json.dumps(event, sort_keys=True), int(time.time())))

    def pending(self, limit: int = 100) -> list[tuple[str, dict[str, Any]]]:
        with self.connect() as db:
            rows = db.execute("SELECT event_id,event_json FROM events WHERE delivered_at IS NULL ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def delivered(self, event_id_value: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE events SET delivered_at=? WHERE event_id=?", (int(time.time()), event_id_value))

    def prune(self, retention_days: int) -> None:
        cutoff = int(time.time()) - retention_days * 86400
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE delivered_at IS NOT NULL AND delivered_at < ?", (cutoff,))


def usage_event(tenant: str, resource: str, domain: str, meter: str, quantity: float, unit: str, start: int, end: int, dimensions: dict[str, Any]) -> dict[str, Any]:
    stable = [tenant, resource, domain, meter, start, end]
    iso = lambda value: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
    return {
        "eventId": event_id(stable), "tenantRef": tenant, "resourceRef": resource,
        "serviceClass": {"compute": "compute.workload.v1", "ceph": "storage.volume.v1", "pigsty": "database.postgresql.shared.v1", "networks": "network.tenant.v1"}[domain],
        "meter": meter, "quantity": quantity, "unit": unit, "periodStart": iso(start), "periodEnd": iso(end),
        "regionRef": "meter-source", "sourceOperationId": "birfucate:" + event_id(stable)[3:], "observedAt": iso(end),
        "schemaVersion": "usage.re8ch.com/v1", "dimensions": dimensions,
        "pricingApplied": False, "accountingPosted": False,
    }


def collect_once(config: dict[str, Any], outbox: Outbox, start: int, end: int) -> None:
    interval = max(1, end - start)
    prom = config["prometheus"]
    tenant_label = config["tenantLabels"]["tenant"]
    resource_label = config["tenantLabels"]["resource"]
    set_metric("birfucate_dependency_up", {"dependency": "prometheus"}, 1)
    opencost = config.get("opencost") or {}
    if opencost.get("enabled"):
        request = urllib.request.Request(opencost["baseURL"].rstrip("/") + opencost["allocationPath"], headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=int(prom["timeoutSeconds"])) as response:
                set_metric("birfucate_dependency_up", {"dependency": "opencost"}, 1 if response.status == 200 else 0)
        except urllib.error.URLError:
            set_metric("birfucate_dependency_up", {"dependency": "opencost"}, 0)
    for meter in config["meters"].get("occupancy", []):
        values = vector(prometheus_query(prom["baseURL"], meter["query"], int(prom["timeoutSeconds"])), meter.get("tenantLabel", tenant_label), meter.get("resourceLabel", resource_label))
        for (tenant, resource), gauge in values.items():
            quantity = gauge * interval
            labels = {"tenant": tenant, "resource_ref": resource, "domain": meter["domain"], "meter": meter["name"], "unit": meter["unit"]}
            set_metric("birfucate_occupancy_current", labels, gauge)
            add_metric("birfucate_occupancy_units_total", labels, quantity)
            outbox.put(usage_event(tenant, resource, meter["domain"], "occupancy." + meter["name"], quantity, meter["unit"], start, end, {"gauge": gauge, "durationSeconds": interval}))
    for meter in config["meters"].get("bifurcation", []):
        counts = vector(prometheus_query(prom["baseURL"], meter["countQuery"], int(prom["timeoutSeconds"])), tenant_label, resource_label)
        intensities = vector(prometheus_query(prom["baseURL"], meter["intensityQuery"], int(prom["timeoutSeconds"])), tenant_label, resource_label)
        fanouts = vector(prometheus_query(prom["baseURL"], meter["fanoutQuery"], int(prom["timeoutSeconds"])), tenant_label, resource_label)
        for (tenant, resource), rate in counts.items():
            occurrences = rate * interval
            intensity = intensities.get((tenant, resource), 0.0)
            fanout = max(1.0, fanouts.get((tenant, resource), 1.0))
            reference = max(float(meter.get("referenceIntensity", 1)), 0.000001)
            strength = math.log2(1.0 + intensity / reference)
            score = occurrences * strength * fanout
            labels = {"tenant": tenant, "resource_ref": resource, "domain": meter["domain"], "meter": meter["name"]}
            add_metric("birfucate_bifurcation_occurrences_total", {**labels, "unit": meter["occurrenceUnit"]}, occurrences)
            set_metric("birfucate_bifurcation_intensity", {**labels, "unit": meter["intensityUnit"]}, intensity)
            set_metric("birfucate_bifurcation_fanout", labels, fanout)
            add_metric("birfucate_bifurcation_score_total", labels, score)
            outbox.put(usage_event(tenant, resource, meter["domain"], "bifurcation." + meter["name"], score, "bifurcation-point", start, end, {"occurrences": occurrences, "occurrenceUnit": meter["occurrenceUnit"], "intensity": intensity, "intensityUnit": meter["intensityUnit"], "referenceIntensity": reference, "fanout": fanout, "strength": strength}))


def deliver(config: dict[str, Any], outbox: Outbox) -> None:
    sink = config.get("usageSink") or {}
    if not sink.get("enabled"):
        return
    headers = {"Content-Type": "application/json", "Idempotency-Key": "eventId"}
    if USAGE_TOKEN:
        headers["Authorization"] = "Bearer " + USAGE_TOKEN
    for key, event in outbox.pending():
        request = urllib.request.Request(sink["url"], data=json.dumps(event).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=int(sink.get("timeoutSeconds", 10))) as response:
                if 200 <= response.status < 300:
                    outbox.delivered(key)
        except urllib.error.URLError:
            break


def render_metrics(timestamp_ms: int | None = None) -> bytes:
    suffix = f" {timestamp_ms}" if timestamp_ms is not None else ""
    lines = ["# HELP birfucate_up Whether the last collection completed.", "# TYPE birfucate_up gauge", f"birfucate_up {1 if READY else 0}{suffix}", "# HELP birfucate_last_success_unixtime Last successful collection time.", "# TYPE birfucate_last_success_unixtime gauge", f"birfucate_last_success_unixtime {LAST_SUCCESS}{suffix}"]
    with LOCK:
        items = sorted(METRICS.items())
    for (name, labels), value in items:
        encoded = ",".join(f'{key}="{str(val).replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))}"' for key, val in labels)
        lines.append(f"{name}{{{encoded}}} {value}{suffix}")
    return ("\n".join(lines) + "\n").encode()


def publish_metrics(config: dict[str, Any]) -> None:
    prom = config["prometheus"]
    path = str(prom.get("writePath") or "").strip()
    if not path:
        return
    request = urllib.request.Request(prom["baseURL"].rstrip("/") + path, data=render_metrics(int(time.time() * 1000)), headers={"Content-Type": "text/plain"}, method="POST")
    with urllib.request.urlopen(request, timeout=int(prom["timeoutSeconds"])) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError("VictoriaMetrics import failed")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/metrics":
            body, status, content_type = render_metrics(), 200, "text/plain; version=0.0.4"
        elif parsed.path == "/healthz":
            body, status, content_type = b'{"status":"ok"}', 200, "application/json"
        elif parsed.path == "/readyz":
            body, status, content_type = (b'{"status":"ready"}', 200, "application/json") if READY else (b'{"status":"not-ready"}', 503, "application/json")
        elif parsed.path == "/api/v1/browser/query":
            params = urllib.parse.parse_qs(parsed.query)
            metric = (params.get("metric") or [""])[0]
            try:
                body = json.dumps(browser_query(load_config(), metric), separators=(",", ":")).encode()
                status, content_type = 200, "application/json"
            except PermissionError as exc:
                body, status, content_type = json.dumps({"status": "error", "error": str(exc)}).encode(), 404, "application/json"
            except ValueError as exc:
                body, status, content_type = json.dumps({"status": "error", "error": str(exc)}).encode(), 400, "application/json"
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                body, status, content_type = json.dumps({"status": "error", "error": str(exc)}).encode(), 502, "application/json"
        else:
            body, status, content_type = b'{"error":"not_found"}', 404, "application/json"
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def loop(config: dict[str, Any], outbox: Outbox) -> None:
    global READY, LAST_SUCCESS
    interval = max(10, int(config.get("collectionIntervalSeconds", 60)))
    end = int(time.time())
    while True:
        start, end = end, int(time.time())
        if end <= start:
            start = end - interval
        try:
            collect_once(config, outbox, start, end)
            READY, LAST_SUCCESS = True, time.time()
            publish_metrics(config)
            deliver(config, outbox)
            outbox.prune(int(config.get("outbox", {}).get("retentionDays", 30)))
        except Exception as exc:
            READY = False
            print(json.dumps({"level": "error", "event": "collection_failed", "error": str(exc)}), flush=True)
        time.sleep(interval)


def main() -> None:
    config, outbox = load_config(), Outbox(OUTBOX_PATH)
    threading.Thread(target=loop, args=(config, outbox), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 9791), Handler).serve_forever()


if __name__ == "__main__":
    main()
