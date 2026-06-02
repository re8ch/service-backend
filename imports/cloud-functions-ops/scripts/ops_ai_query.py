#!/usr/bin/env python3
"""Read-only ops query helper for AI agents.

The tool intentionally avoids privileged mutations. Kubernetes data is read via
kubectl JSON output, while Grafana panel data is queried at the VictoriaMetrics
Prometheus-compatible API layer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_VM_URL = os.environ.get("OPS_VM_URL", "http://127.0.0.1:8428").rstrip("/")
DEFAULT_KUBECTL = os.environ.get("KUBECTL_CMD", "kubectl")


PROMQL_TEMPLATES = {
    "up": 'up',
    "node_cpu_percent": '100 * (1 - avg by (instance, node) (rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "node_mem_percent": '100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))',
    "node_disk_avail_percent": '100 * (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"})',
    "kube_pod_restarts_15m": 'sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total[15m]))',
    "kube_pod_not_ready": 'sum by (namespace, pod) (kube_pod_status_ready{condition="false"})',
    "pg_up": 'pg_up',
    "pg_connections": 'pg_backends',
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def run_cmd(cmd: list[str], timeout: int = 20) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "command failed").strip()
        raise RuntimeError(f"{' '.join(cmd)}: {detail}")
    return proc.stdout


def kubectl(args: list[str], timeout: int = 25) -> Any:
    cmd = shlex.split(DEFAULT_KUBECTL) + args
    return json.loads(run_cmd(cmd, timeout=timeout))


def k8s_nodes() -> dict[str, Any]:
    data = kubectl(["get", "nodes", "-o", "json"])
    items = []
    for node in data.get("items", []):
        meta = node.get("metadata", {})
        status = node.get("status", {})
        conditions = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
        allocatable = status.get("allocatable", {})
        items.append({
            "name": meta.get("name"),
            "ready": conditions.get("Ready") == "True",
            "conditions": conditions,
            "roles": sorted(k for k in meta.get("labels", {}) if k.startswith("node-role.kubernetes.io/")),
            "labels": meta.get("labels", {}),
            "taints": node.get("spec", {}).get("taints", []),
            "allocatable": {
                "cpu": allocatable.get("cpu"),
                "memory": allocatable.get("memory"),
                "pods": allocatable.get("pods"),
            },
            "created": meta.get("creationTimestamp"),
        })
    return {"timestamp": now_iso(), "nodes": items}


def k8s_pods(namespace: str | None, all_namespaces: bool, selector: str | None) -> dict[str, Any]:
    args = ["get", "pods"]
    if all_namespaces:
        args.append("-A")
    elif namespace:
        args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["-o", "json"])
    data = kubectl(args)
    pods = []
    for pod in data.get("items", []):
        meta = pod.get("metadata", {})
        status = pod.get("status", {})
        restarts = 0
        containers = []
        for c in status.get("containerStatuses", []) or []:
            restarts += int(c.get("restartCount") or 0)
            state = c.get("state") or {}
            containers.append({
                "name": c.get("name"),
                "ready": c.get("ready"),
                "restartCount": c.get("restartCount"),
                "state": next(iter(state.keys()), "unknown"),
                "reason": next(iter(state.values()), {}).get("reason") if state else None,
            })
        pods.append({
            "namespace": meta.get("namespace"),
            "name": meta.get("name"),
            "node": pod.get("spec", {}).get("nodeName"),
            "phase": status.get("phase"),
            "podIP": status.get("podIP"),
            "restarts": restarts,
            "containers": containers,
            "created": meta.get("creationTimestamp"),
        })
    return {"timestamp": now_iso(), "pods": pods}


def k8s_events(namespace: str | None, all_namespaces: bool, limit: int) -> dict[str, Any]:
    args = ["get", "events"]
    if all_namespaces:
        args.append("-A")
    elif namespace:
        args.extend(["-n", namespace])
    args.extend(["--sort-by=.lastTimestamp", "-o", "json"])
    data = kubectl(args)
    events = []
    for event in data.get("items", [])[-limit:]:
        meta = event.get("metadata", {})
        events.append({
            "namespace": meta.get("namespace"),
            "type": event.get("type"),
            "reason": event.get("reason"),
            "message": event.get("message"),
            "object": event.get("involvedObject", {}),
            "count": event.get("count"),
            "firstTimestamp": event.get("firstTimestamp") or event.get("eventTime"),
            "lastTimestamp": event.get("lastTimestamp") or event.get("eventTime"),
        })
    return {"timestamp": now_iso(), "events": events}


def http_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def vm_query(vm_url: str, query: str, ts: float | None = None) -> dict[str, Any]:
    params = {"query": query}
    if ts is not None:
        params["time"] = str(ts)
    url = f"{vm_url}/api/v1/query?{urllib.parse.urlencode(params)}"
    payload = http_json(url)
    return {
        "timestamp": now_iso(),
        "vmUrl": vm_url,
        "query": query,
        "resultType": payload.get("data", {}).get("resultType"),
        "result": payload.get("data", {}).get("result", []),
        "status": payload.get("status"),
    }


def vm_range(vm_url: str, query: str, start: float, end: float, step: str) -> dict[str, Any]:
    params = {"query": query, "start": str(start), "end": str(end), "step": step}
    url = f"{vm_url}/api/v1/query_range?{urllib.parse.urlencode(params)}"
    payload = http_json(url)
    return {
        "timestamp": now_iso(),
        "vmUrl": vm_url,
        "query": query,
        "start": start,
        "end": end,
        "step": step,
        "resultType": payload.get("data", {}).get("resultType"),
        "result": payload.get("data", {}).get("result", []),
        "status": payload.get("status"),
    }


def k8s_summary(namespace: str | None) -> dict[str, Any]:
    nodes = k8s_nodes()["nodes"]
    pods = k8s_pods(namespace=namespace, all_namespaces=namespace is None, selector=None)["pods"]
    bad_pods = []
    for pod in pods:
        if pod["phase"] not in {"Running", "Succeeded"}:
            bad_pods.append(pod)
            continue
        if any(not c.get("ready") and pod["phase"] == "Running" for c in pod["containers"]):
            bad_pods.append(pod)
    return {
        "timestamp": now_iso(),
        "nodes": {
            "total": len(nodes),
            "notReady": [n for n in nodes if not n["ready"]],
        },
        "pods": {
            "total": len(pods),
            "unhealthy": bad_pods[:100],
        },
    }


def health(vm_url: str, namespace: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "timestamp": now_iso(),
        "k3s": None,
        "metrics": {},
        "errors": [],
    }
    try:
        out["k3s"] = k8s_summary(namespace)
    except Exception as exc:
        out["errors"].append({"source": "k3s", "error": str(exc)})
    for name in ("up", "node_cpu_percent", "node_mem_percent", "kube_pod_restarts_15m"):
        try:
            out["metrics"][name] = vm_query(vm_url, PROMQL_TEMPLATES[name])
        except Exception as exc:
            out["errors"].append({"source": f"metrics:{name}", "error": str(exc)})
    return out


def resolve_query(name_or_query: str) -> str:
    return PROMQL_TEMPLATES.get(name_or_query, name_or_query)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only K3S and VictoriaMetrics query helper for AI agents.")
    parser.add_argument("--vm-url", default=DEFAULT_VM_URL, help="VictoriaMetrics base URL. Default: OPS_VM_URL or http://127.0.0.1:8428")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("templates", help="List built-in PromQL templates.")
    sub.add_parser("nodes", help="List K3S nodes.")

    pods = sub.add_parser("pods", help="List K3S pods.")
    pods.add_argument("-n", "--namespace")
    pods.add_argument("-A", "--all-namespaces", action="store_true")
    pods.add_argument("-l", "--selector")

    events = sub.add_parser("events", help="List K3S events.")
    events.add_argument("-n", "--namespace")
    events.add_argument("-A", "--all-namespaces", action="store_true")
    events.add_argument("--limit", type=int, default=50)

    summary = sub.add_parser("summary", help="Summarize K3S nodes and unhealthy pods.")
    summary.add_argument("-n", "--namespace")

    query = sub.add_parser("query", help="Run an instant PromQL query or built-in template.")
    query.add_argument("promql")

    rng = sub.add_parser("range", help="Run a range PromQL query or built-in template.")
    rng.add_argument("promql")
    rng.add_argument("--minutes", type=int, default=60)
    rng.add_argument("--step", default="60s")

    h = sub.add_parser("health", help="Collect a compact K3S + metrics health snapshot.")
    h.add_argument("-n", "--namespace")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.cmd == "templates":
            json_dump({"templates": PROMQL_TEMPLATES})
        elif args.cmd == "nodes":
            json_dump(k8s_nodes())
        elif args.cmd == "pods":
            json_dump(k8s_pods(args.namespace, args.all_namespaces, args.selector))
        elif args.cmd == "events":
            json_dump(k8s_events(args.namespace, args.all_namespaces, args.limit))
        elif args.cmd == "summary":
            json_dump(k8s_summary(args.namespace))
        elif args.cmd == "query":
            json_dump(vm_query(args.vm_url.rstrip("/"), resolve_query(args.promql)))
        elif args.cmd == "range":
            end = time.time()
            start = end - args.minutes * 60
            json_dump(vm_range(args.vm_url.rstrip("/"), resolve_query(args.promql), start, end, args.step))
        elif args.cmd == "health":
            json_dump(health(args.vm_url.rstrip("/"), args.namespace))
        return 0
    except Exception as exc:
        json_dump({"timestamp": now_iso(), "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
