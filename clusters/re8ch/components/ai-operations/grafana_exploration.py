#!/usr/bin/env python3
"""Tenant-bound Grafana querying and deterministic time-series analysis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable


class ExplorationError(ValueError):
    pass


MAX_QUERIES = 8
MAX_RANGE_MS = 31 * 24 * 60 * 60 * 1000
MAX_POINTS_PER_SERIES = 5_000
MAX_TOTAL_POINTS = 50_000
RESULT_TTL_SECONDS = 900

TENANT_GRAFANA = {
    "qwen": {"orgId": 2, "namespace": "qwen", "tokenSecret": "grafana-qianwen-viewer", "tenantLabel": "qianwen"},
    "qianwen": {"orgId": 2, "namespace": "qwen", "tokenSecret": "grafana-qianwen-viewer", "tenantLabel": "qianwen"},
    "ledger": {"orgId": 3, "namespace": "ledger", "tokenSecret": "grafana-ledger-viewer", "tenantLabel": "ledger"},
    "re8ch": {"orgId": 4, "namespace": "qianwen-ops", "tokenSecret": "grafana-re8ch-viewer", "tenantLabel": "re8ch"},
}


def tenant_config(tenant: str) -> dict[str, Any]:
    dynamic = os.getenv("GRAFANA_TENANT_BINDINGS_JSON", "")
    if dynamic:
        try:
            configured = json.loads(dynamic)
            if tenant in configured:
                value = configured[tenant]
                if not isinstance(value.get("orgId"), int) or not value.get("tokenEnv") or not value.get("tenantLabel"):
                    raise ExplorationError("invalid server-side Grafana tenant binding")
                return dict(value)
        except json.JSONDecodeError as exc:
            raise ExplorationError("invalid server-side Grafana tenant bindings") from exc
    try:
        return dict(TENANT_GRAFANA[tenant])
    except KeyError as exc:
        raise ExplorationError("tenant has no Grafana organization binding") from exc


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"(\d+)(ms|s|m|h|d|w)", value)
    if not match:
        raise ExplorationError("invalid duration")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[unit]


def parse_time(value: Any, current_ms: int | None = None) -> int:
    current_ms = current_ms or int(time.time() * 1000)
    if isinstance(value, int):
        return value
    if value == "now":
        return current_ms
    match = re.fullmatch(r"now-(\d+(?:ms|s|m|h|d|w))", str(value))
    if match:
        return current_ms - parse_duration(match.group(1))
    raise ExplorationError("time must be epoch milliseconds, now, or now-<duration>")


def _query_text(query: dict[str, Any]) -> str:
    model = query.get("model") or {}
    return str(model.get("expr") or model.get("query") or model.get("rawSql") or "")


def validate_query_spec(spec: dict[str, Any], tenant: str, datasource_types: dict[str, str]) -> tuple[int, int]:
    if "orgId" in spec or "organizationId" in spec:
        raise ExplorationError("organization identifiers are derived from the authenticated tenant")
    queries = spec.get("queries") or []
    if not 1 <= len(queries) <= MAX_QUERIES:
        raise ExplorationError("queries must contain between 1 and 8 entries")
    now_ms = int(time.time() * 1000)
    time_range = spec.get("timeRange") or {}
    start, end = parse_time(time_range.get("from", "now-1h"), now_ms), parse_time(time_range.get("to", "now"), now_ms)
    if start >= end or end - start > MAX_RANGE_MS:
        raise ExplorationError("time range must be positive and no longer than 31 days")
    seen: set[str] = set()
    tenant_label = tenant_config(tenant)["tenantLabel"]
    for query in queries:
        uid, ref_id = str(query.get("datasourceUid", "")), str(query.get("refId", ""))
        if uid not in datasource_types:
            raise ExplorationError("datasource is not visible to the authenticated tenant")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,15}", ref_id) or ref_id in seen:
            raise ExplorationError("query refId must be unique and identifier-like")
        seen.add(ref_id)
        points = int(query.get("maxDataPoints", 1000))
        if not 1 <= points <= MAX_POINTS_PER_SERIES:
            raise ExplorationError("maxDataPoints must be between 1 and 5000")
        kind, text = str(query.get("queryKind", "")).lower(), _query_text(query)
        ds_type = datasource_types[uid].lower()
        if kind in {"promql", "logsql"} or "prometheus" in ds_type or "victoriametrics" in ds_type:
            if not re.search(r"tenant\s*=~?\s*[\"']" + re.escape(tenant_label) + r"[\"']", text):
                raise ExplorationError("tenant matcher is required in metrics and logs queries")
        if kind == "sql" or ds_type in {"postgres", "mysql", "mssql"}:
            validate_sql(text)
    return start, end


def validate_sql(sql: str) -> None:
    normalized = sql.strip()
    if not re.match(r"(?is)^(select|with)\b", normalized):
        raise ExplorationError("SQL must be a SELECT or WITH query")
    if ";" in normalized.rstrip(";") or re.search(r"(--|/\*|\*/|\b(copy|call|do|execute|insert|update|delete|alter|drop|create|grant|revoke|truncate)\b)", normalized, re.I):
        raise ExplorationError("SQL contains a forbidden statement or token")


@dataclass
class GrafanaClient:
    base_url: str
    authorization: str
    org_id: int

    def request(self, method: str, path: str, body: Any | None = None, *, timeout: int = 30, raw: bool = False) -> Any:
        headers = {"Accept": "application/json", "Authorization": self.authorization, "X-Grafana-Org-Id": str(self.org_id)}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                return payload if raw else json.loads(payload.decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise ExplorationError(f"Grafana request failed with HTTP {exc.code}") from exc


class ResultStore:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str, Any]] = {}

    def put(self, tenant: str, value: Any) -> str:
        self.purge()
        ref = "gr_" + uuid.uuid4().hex
        self._values[ref] = (time.time() + RESULT_TTL_SECONDS, tenant, value)
        return ref

    def get(self, tenant: str, ref: str) -> Any:
        self.purge()
        record = self._values.get(ref)
        if not record or record[1] != tenant:
            raise ExplorationError("result reference is unavailable")
        return record[2]

    def purge(self) -> None:
        current = time.time()
        self._values = {key: value for key, value in self._values.items() if value[0] > current}


RESULTS = ResultStore()


def datasource_capability(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("type", ""))
    query_kinds = []
    if "prometheus" in kind or "victoriametrics-metrics" in kind:
        query_kinds = ["promql"]
    elif "logs" in kind or kind == "loki":
        query_kinds = ["logsql"]
    elif kind in {"postgres", "mysql", "mssql"}:
        query_kinds = ["sql"]
    elif kind == "jaeger":
        query_kinds = ["trace-search"]
    return {
        "uid": item.get("uid"), "name": item.get("name"), "type": kind,
        "queryKinds": query_kinds, "minimumInterval": (item.get("jsonData") or {}).get("timeInterval", "1s"),
        "maximumRange": "31d", "readOnly": True,
    }


def normalize_frames(payload: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    total = 0
    for ref_id, result in (payload.get("results") or {}).items():
        frames = []
        for frame in result.get("frames") or []:
            schema = frame.get("schema") or {}
            data = frame.get("data") or {}
            values = data.get("values") or []
            total += max((len(column) for column in values), default=0)
            frames.append({"schema": schema, "data": data})
        results[ref_id] = {"frames": frames, "error": result.get("error")}
    if total > MAX_TOTAL_POINTS:
        raise ExplorationError("query returned more than 50000 points; increase interval or reduce the range")
    return {"results": results, "pointCount": total}


def frames_to_series(result: dict[str, Any]) -> dict[str, list[float]]:
    output: dict[str, list[float]] = {}
    for ref_id, item in (result.get("results") or {}).items():
        for frame_index, frame in enumerate(item.get("frames") or []):
            fields = (frame.get("schema") or {}).get("fields") or []
            columns = (frame.get("data") or {}).get("values") or []
            for field_index, column in enumerate(columns):
                field = fields[field_index] if field_index < len(fields) else {}
                if field.get("type") not in {"number", "integer", "float"}:
                    continue
                values = [float(value) for value in column if isinstance(value, (int, float)) and math.isfinite(float(value))]
                if values:
                    output[f"{ref_id}.{field.get('name') or field_index}.{frame_index}"] = values
    return output


def execute_query(client: GrafanaClient, tenant: str, spec: dict[str, Any], datasources: list[dict[str, Any]]) -> dict[str, Any]:
    types = {str(item.get("uid")): str(item.get("type")) for item in datasources}
    by_uid = {str(item.get("uid")): item for item in datasources}
    start, end = validate_query_spec(spec, tenant, types)
    queries = []
    for entry in spec["queries"]:
        model = dict(entry.get("model") or {})
        max_points = int(entry.get("maxDataPoints", 1000))
        minimum = parse_duration(str((by_uid[entry["datasourceUid"]].get("jsonData") or {}).get("timeInterval", "1s")))
        requested = entry.get("intervalMs")
        if requested is not None and int(requested) < minimum:
            raise ExplorationError("intervalMs is below the datasource minimum interval")
        interval = max(minimum, math.ceil((end - start) / max_points), int(requested or 0))
        model.update({"refId": entry["refId"], "datasource": {"uid": entry["datasourceUid"]}, "maxDataPoints": max_points, "intervalMs": interval})
        queries.append(model)
    payload = client.request("POST", "/api/ds/query", {"from": str(start), "to": str(end), "queries": queries})
    normalized = normalize_frames(payload)
    result = {**normalized, "timeRange": {"from": start, "to": end, "timezone": (spec.get("timeRange") or {}).get("timezone", "UTC")}, "queryHash": stable_hash(spec), "warnings": []}
    result["resultRef"] = RESULTS.put(tenant, result)
    result["resultUri"] = f"re8ch://tenant/{tenant}/grafana/results/{result['resultRef']}"
    return result


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    pos = 0
    while pos < len(ordered):
        end = pos + 1
        while end < len(ordered) and ordered[end][1] == ordered[pos][1]:
            end += 1
        rank = (pos + end - 1) / 2 + 1
        for index in range(pos, end):
            result[ordered[index][0]] = rank
        pos = end
    return result


def correlation(left: list[float], right: list[float], method: str = "pearson") -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    if method == "spearman":
        left, right = _rank(left), _rank(right)
    mean_l, mean_r = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((a - mean_l) * (b - mean_r) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - mean_l) ** 2 for a in left) * sum((b - mean_r) ** 2 for b in right))
    return numerator / denominator if denominator else None


def indicators(values: list[float], requested: list[dict[str, Any]]) -> dict[str, Any]:
    if not values:
        raise ExplorationError("indicator input is empty")
    output: dict[str, Any] = {}
    for item in requested:
        kind, name = item["type"], item.get("outputName") or item["type"]
        window = max(1, int(item.get("window", 5)))
        if kind == "movingAverage":
            output[name] = [statistics.fmean(values[max(0, i-window+1):i+1]) for i in range(len(values))]
        elif kind == "ema":
            alpha, series = float(item.get("alpha", 2 / (window + 1))), [values[0]]
            series.extend(alpha * value + (1-alpha) * series[-1] for value in values[1:])
            output[name] = series
        elif kind == "changeRate":
            output[name] = [None] + [(b-a)/abs(a) if a else None for a, b in zip(values, values[1:])]
        elif kind == "volatility":
            changes = [0.0] + [(b-a)/abs(a) if a else 0.0 for a, b in zip(values, values[1:])]
            output[name] = [statistics.pstdev(changes[max(0, i-window+1):i+1]) for i in range(len(changes))]
        elif kind == "zscore":
            mean, deviation = statistics.fmean(values), statistics.pstdev(values)
            output[name] = [(value-mean)/deviation if deviation else 0.0 for value in values]
        elif kind == "slope":
            xs, mx, my = list(range(len(values))), (len(values)-1)/2, statistics.fmean(values)
            denom = sum((x-mx)**2 for x in xs)
            slope = sum((x-mx)*(y-my) for x, y in zip(xs, values))/denom if denom else 0.0
            output[name] = {"slope": slope, "changeRate": (values[-1]-values[0])/abs(values[0]) if values[0] else None, "trend": "up" if slope > 0 else "down" if slope < 0 else "flat"}
        elif kind == "quantile":
            q = float(item.get("quantile", .95)); ordered = sorted(values)
            output[name] = ordered[min(len(ordered)-1, max(0, round(q*(len(ordered)-1))))]
        else:
            raise ExplorationError("unsupported indicator")
    return output


def analyze_series(series: dict[str, list[float]], pipeline: list[dict[str, Any]]) -> dict[str, Any]:
    output = {key: list(value) for key, value in series.items()}
    summaries: dict[str, Any] = {}
    for step in pipeline:
        kind, inputs = step["type"], step.get("inputs") or list(output)
        if kind == "reduce":
            operation = step["operation"]
            fn: Callable[[list[float]], float] = {"avg": statistics.fmean, "sum": sum, "min": min, "max": max, "count": lambda x: float(len(x))}[operation]
            summaries[step.get("outputName", operation)] = {key: fn(output[key]) for key in inputs}
        elif kind in {"merge", "math"}:
            rows = list(zip(*(output[key] for key in inputs)))
            operation = step.get("operation", "sum")
            output[step.get("outputName", "merged")] = [statistics.fmean(row) if operation == "avg" else sum(row) for row in rows]
        elif kind == "normalize":
            for key in inputs:
                mean, deviation = statistics.fmean(output[key]), statistics.pstdev(output[key])
                output[key] = [(value-mean)/deviation if deviation else 0.0 for value in output[key]]
        elif kind == "filter":
            minimum, maximum = step.get("min"), step.get("max")
            for key in inputs:
                output[key] = [value for value in output[key] if (minimum is None or value >= minimum) and (maximum is None or value <= maximum)]
        else:
            raise ExplorationError("unsupported analysis pipeline step")
    return {"series": output, "summaries": summaries}


def lag_correlations(left: list[float], right: list[float], method: str, max_lag: int) -> list[dict[str, Any]]:
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        a, b = (left[-lag:], right[:len(right)+lag]) if lag < 0 else (left[:len(left)-lag or None], right[lag:])
        rows.append({"lag": lag, "coefficient": correlation(a, b, method), "sampleCount": len(a), "missingRate": 0.0})
    return rows
