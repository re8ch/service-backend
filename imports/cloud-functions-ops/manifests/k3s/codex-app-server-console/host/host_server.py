#!/usr/bin/env python3
"""ZeroTier-bound fallback console that remains reachable when K3S routing fails."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BIND_ADDRESS = os.getenv("BIND_ADDRESS", "192.0.2.183")
PORT = int(os.getenv("PORT", "18082"))
KUBECTL = os.getenv(
    "KUBECTL",
    "/usr/local/bin/k3s kubectl --kubeconfig /etc/re8ch/codex-fleet/kubeconfig",
).split()
INDEX = Path(__file__).with_name("index.html").read_text(encoding="utf-8").replace(
    "__APP_BASE_PATH__", ""
)
STARTED_AT = dt.datetime.now(dt.timezone.utc)


def kubectl_json(args: list[str], timeout: int = 8) -> dict:
    proc = subprocess.run(
        [*KUBECTL, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return json.loads(proc.stdout)


def is_app_server(deployment: dict) -> bool:
    metadata = deployment.get("metadata", {})
    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})
    containers = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    command_text = " ".join(
        " ".join(container.get("command", []) + container.get("args", []))
        for container in containers
    )
    return (
        "codex-app-server" in metadata.get("name", "")
        or labels.get("app.kubernetes.io/component") == "app-server"
        or annotations.get("codex.re8ch.com/managed") == "true"
        or "codex app-server" in command_text
    )


def labels_match(selector: dict, labels: dict) -> bool:
    return bool(selector) and all(labels.get(key) == value for key, value in selector.items())


def task_activity(ready: bool, value: str, terminating: bool) -> str:
    if terminating:
        return "terminating"
    if not ready:
        return "unhealthy"
    if value in {"queued", "pending", "排队"}:
        return "queued"
    if value in {"running", "working", "处理中", "busy"}:
        return "busy"
    if value in {"blocked", "waiting", "等待输入"}:
        return "blocked"
    return "idle"


def fleet_snapshot() -> dict:
    resource_list = kubectl_json(
        ["get", "deployments.apps,pods,services", "-A", "-o", "json"], timeout=8
    )
    items = resource_list.get("items", [])
    deployments = [
        item
        for item in items
        if item.get("kind") == "Deployment" and is_app_server(item)
    ]
    pods = [item for item in items if item.get("kind") == "Pod"]
    services = [item for item in items if item.get("kind") == "Service"]
    instances = []

    for deployment in deployments:
        metadata = deployment.get("metadata", {})
        namespace = metadata.get("namespace", "default")
        name = metadata.get("name", "unknown")
        selector = (
            deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
        )
        related = [
            pod
            for pod in pods
            if pod.get("metadata", {}).get("namespace") == namespace
            and labels_match(selector, pod.get("metadata", {}).get("labels", {}))
        ]
        related.sort(
            key=lambda pod: pod.get("metadata", {}).get("creationTimestamp", ""),
            reverse=True,
        )
        pod = next(
            (
                candidate
                for candidate in related
                if not candidate.get("metadata", {}).get("deletionTimestamp")
            ),
            related[0] if related else {},
        )
        pod_meta = pod.get("metadata", {})
        pod_status = pod.get("status", {})
        conditions = pod_status.get("conditions", [])
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in conditions
        )
        statuses = pod_status.get("containerStatuses", [])
        restarts = sum(status.get("restartCount", 0) for status in statuses)
        annotations = {
            **metadata.get("annotations", {}),
            **pod_meta.get("annotations", {}),
        }
        task_state = annotations.get("codex.re8ch.com/task-state", "")
        health = (
            "healthy"
            if ready
            else "degraded"
            if pod_status.get("phase") == "Running"
            else "offline"
        )
        service = next(
            (
                candidate
                for candidate in services
                if candidate.get("metadata", {}).get("namespace") == namespace
                and labels_match(
                    candidate.get("spec", {}).get("selector", {}),
                    pod_meta.get("labels", {}),
                )
            ),
            None,
        )
        containers = (
            deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        instances.append(
            {
                "id": f"{namespace}/{name}",
                "namespace": namespace,
                "name": name,
                "pod": pod_meta.get("name"),
                "node": pod.get("spec", {}).get("nodeName"),
                "image": containers[0].get("image") if containers else None,
                "replicas": {
                    "desired": deployment.get("spec", {}).get("replicas", 0),
                    "ready": deployment.get("status", {}).get("readyReplicas", 0),
                    "available": deployment.get("status", {}).get(
                        "availableReplicas", 0
                    ),
                },
                "health": health,
                "activity": task_activity(
                    ready, task_state, bool(pod_meta.get("deletionTimestamp"))
                ),
                "phase": pod_status.get("phase", "Pending"),
                "restarts": restarts,
                "createdAt": pod_meta.get("creationTimestamp")
                or metadata.get("creationTimestamp"),
                "owner": annotations.get("codex.re8ch.com/owner")
                or annotations.get("re8ch.com/owner")
                or "未标注",
                "userId": annotations.get("codex.re8ch.com/user-id"),
                "task": {
                    "id": annotations.get("codex.re8ch.com/task-id"),
                    "title": annotations.get("codex.re8ch.com/task-title"),
                    "state": task_state or None,
                    "stage": annotations.get("codex.re8ch.com/task-stage"),
                    "updatedAt": annotations.get(
                        "codex.re8ch.com/task-updated-at"
                    ),
                    "threadId": annotations.get("codex.re8ch.com/thread-id"),
                },
                "usage": {"cpuMillicores": 0, "memoryMiB": 0},
                "endpoint": (
                    {
                        "service": (
                            f"{service['metadata']['name']}.{namespace}."
                            "svc.cluster.local"
                        ),
                        "port": service.get("spec", {})
                        .get("ports", [{}])[0]
                        .get("port", 4500),
                    }
                    if service
                    else None
                ),
                "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )

    order = {"busy": 0, "blocked": 1, "queued": 2, "unhealthy": 3, "idle": 4}
    instances.sort(key=lambda item: (order.get(item["activity"], 9), item["id"]))
    summary = {
        "total": len(instances),
        "healthy": 0,
        "degraded": 0,
        "offline": 0,
        "busy": 0,
        "blocked": 0,
        "queued": 0,
        "idle": 0,
        "cpuMillicores": 0,
        "memoryMiB": 0,
    }
    for instance in instances:
        summary[instance["health"]] = summary.get(instance["health"], 0) + 1
        summary[instance["activity"]] = summary.get(instance["activity"], 0) + 1
    return {
        "summary": summary,
        "instances": instances,
        "scope": "operator",
        "transport": "zerotier-host",
        "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexFleetInternal/1.0"

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in {"/healthz", "/readyz"}:
            return self.send_json(
                200,
                {
                    "ok": True,
                    "transport": "zerotier-host",
                    "uptimeSeconds": int(
                        (dt.datetime.now(dt.timezone.utc) - STARTED_AT).total_seconds()
                    ),
                },
            )
        if route == "/api/auth":
            return self.send_json(
                200,
                {
                    "authenticated": True,
                    "scope": "operator",
                    "identity": {
                        "sub": "internal-operator",
                        "email": None,
                        "name": "ZeroTier 内网运营",
                    },
                },
            )
        if route == "/api/instances":
            try:
                return self.send_json(200, fleet_snapshot())
            except subprocess.TimeoutExpired:
                return self.send_json(
                    503,
                    {
                        "error": "k3s_apiserver_timeout",
                        "detail": "控制台可达，但 K3S API 暂时无响应",
                    },
                )
            except Exception as error:
                return self.send_json(
                    503,
                    {"error": "fleet_discovery_failed", "detail": str(error)},
                )
        if route in {"/", "/index.html"}:
            body = INDEX.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'self'",
            )
            self.end_headers()
            return self.wfile.write(body)
        self.send_json(404, {"error": "not_found"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(
            json.dumps(
                {
                    "time": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "source": self.client_address[0],
                    "message": fmt % args,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    print(
        json.dumps(
            {"event": "listening", "address": BIND_ADDRESS, "port": PORT},
            ensure_ascii=False,
        ),
        flush=True,
    )
    ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler).serve_forever()
