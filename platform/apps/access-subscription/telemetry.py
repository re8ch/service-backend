#!/usr/bin/env python3
"""Aggregate Mihomo connection counters by authenticated inbound user."""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NAMESPACE = os.getenv("POD_NAMESPACE", "egress-fabric")
USAGE_NAME = os.getenv("USAGE_CONFIGMAP", "access-device-usage")
REGION = os.getenv("ACCESS_REGION", "unknown")
CONTROLLER = os.getenv("MIHOMO_CONTROLLER", "http://127.0.0.1:9090")
CONTROLLER_SECRET_PATH = Path(os.getenv("MIHOMO_CONTROLLER_SECRET_PATH", "/controller/secret"))


def kube_request(method: str, path: str, body: dict | None = None) -> dict:
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"https://{host}:{port}{path}", data=raw, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.load(response) if response.length != 0 else {}


def connections() -> list[dict]:
    secret = CONTROLLER_SECRET_PATH.read_text().strip()
    request = urllib.request.Request(f"{CONTROLLER}/connections", headers={"Authorization": f"Bearer {secret}"})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response).get("connections", [])


def accumulate(state: dict, snapshot: list[dict], now: int | None = None) -> dict:
    timestamp = int(time.time() if now is None else now)
    month = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")
    if state.get("month") != month:
        state = {"month": month, "connections": {}, "devices": {}}
    old_connections = state.setdefault("connections", {})
    devices = state.setdefault("devices", {})
    active = {}
    for connection in snapshot:
        metadata = connection.get("metadata", {})
        user = str(metadata.get("inboundUser", "")).strip()
        connection_id = str(connection.get("id", "")).strip()
        if not user or not connection_id:
            continue
        upload, download = int(connection.get("upload", 0)), int(connection.get("download", 0))
        previous = old_connections.get(connection_id, {})
        item = devices.setdefault(user, {"upload": 0, "download": 0})
        item["upload"] = int(item.get("upload", 0)) + max(0, upload - int(previous.get("upload", 0)))
        item["download"] = int(item.get("download", 0)) + max(0, download - int(previous.get("download", 0)))
        item["lastSeenAt"] = timestamp
        item["activeConnections"] = int(item.get("activeConnections", 0)) + 1
        active[connection_id] = {"upload": upload, "download": download, "user": user, "seenAt": timestamp}
    for item in devices.values():
        item["activeConnections"] = 0
    for item in active.values():
        devices[item["user"]]["activeConnections"] += 1
    state["connections"] = active
    state["updatedAt"] = timestamp
    return state


def persist(snapshot: list[dict], attempts: int = 5) -> None:
    key = f"usage-{REGION}.json"
    for _ in range(attempts):
        item = kube_request("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{USAGE_NAME}")
        data = item.get("data", {})
        state = json.loads(data.get(key, "{}"))
        item["data"] = {**data, key: json.dumps(accumulate(state, snapshot), separators=(",", ":"), sort_keys=True)}
        item.pop("status", None)
        try:
            kube_request("PUT", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{USAGE_NAME}", item)
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
    raise RuntimeError("usage state update conflict")


if __name__ == "__main__":
    while True:
        try:
            persist(connections())
        except Exception as exc:
            print(json.dumps({"level": "warning", "event": "telemetry-poll-failed", "error": type(exc).__name__}), flush=True)
        time.sleep(5)
