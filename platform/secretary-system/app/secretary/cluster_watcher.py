from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .model import utc_now


TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


def request_json(url: str, *, token: str | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if raw is not None:
        timestamp = str(int(time.time()))
        signature = hmac.new(os.environ["SECRETARY_WEBHOOK_SECRET"].encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        headers.update({"Content-Type": "application/json", "X-Secretary-Timestamp": timestamp, "X-Secretary-Signature": signature})
    context = ssl.create_default_context(cafile=CA_PATH) if url.startswith("https://kubernetes") else None
    request = urllib.request.Request(url, data=raw, method="POST" if raw is not None else "GET", headers=headers)
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        return json.loads(response.read() or b"{}")


def incident(service: str, transition: str, severity: str, description: str, failure_domain: str) -> None:
    fingerprint = hashlib.sha256(f"{service}:{transition}".encode()).hexdigest()
    incident_id = f"inc_cluster_{fingerprint[:24]}"
    recipient_id = os.environ.get("SECRETARY_NOTIFY_RECIPIENT_ID", "cluster-owner")
    payload = {
        "incident_id": incident_id, "service": service, "severity": severity, "source": "secretary-cluster-watcher",
        "observed_at": utc_now(), "failure_domain": failure_domain,
        "artifact_ref": f"kubernetes:{service}:{transition}", "idempotency_key": f"cluster-health:{fingerprint}",
        "ack_timeout_seconds": int(os.environ.get("SECRETARY_ACK_TIMEOUT_SECONDS", "900")),
        "investigation_timeout_seconds": 3600,
        "responsibility_contract": {
            "objective": description, "assignee_id": recipient_id, "allowed_tools": ["read-only-cluster-observation"],
            "forbidden_actions": ["cluster-write", "incident-close-without-evidence"],
            "required_evidence": ["human-ack", "two-independent-recovery-observations"],
            "deadline_seconds": 3600, "max_model_cost_usd": 1, "max_phone_calls": 0,
            "escalation_target": "cluster-owner", "completion_definition": "verified recovery",
        },
        "notification_email": {"recipient_id": recipient_id, "to": os.environ["SECRETARY_NOTIFY_TO"],
            "subject": f"[Secretary/{severity.upper()}] {service}", "text": description},
    }
    request_json(os.environ.get("SECRETARY_API_URL", "http://secretary-api/v1/incidents"), body=payload)


def observe_nodes() -> None:
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    api = request_json("https://kubernetes.default.svc/api/v1/nodes", token=token)
    for node in api.get("items", []):
        ready = next((condition for condition in node.get("status", {}).get("conditions", []) if condition.get("type") == "Ready"), {})
        if ready.get("status") != "True":
            name = node["metadata"]["name"]
            transition = ready.get("lastTransitionTime") or node["metadata"].get("resourceVersion", "unknown")
            incident(f"node/{name}", transition, "high", f"Kubernetes node {name} is {ready.get('status', 'Unknown')}: {ready.get('reason', 'unknown')}", f"node-{name}")


def observe_public_probes(failures: dict[str, int]) -> None:
    for probe in json.loads(os.environ.get("SECRETARY_PUBLIC_PROBES_JSON", "[]")):
        url, name = probe["url"], probe.get("name") or probe["url"]
        try:
            with urllib.request.urlopen(url, timeout=int(probe.get("timeout_seconds", 10))) as response:
                if response.status >= 500:
                    raise RuntimeError(f"HTTP {response.status}")
            failures[name] = 0
        except Exception as exc:
            failures[name] = failures.get(name, 0) + 1
            if failures[name] == int(probe.get("failure_threshold", 3)):
                bucket = str(int(time.time()) // 300)
                incident(f"public/{name}", bucket, probe.get("severity", "high"), f"Public probe {url} failed: {type(exc).__name__}", f"external-{name}")


def main() -> None:
    failures: dict[str, int] = {}
    while True:
        try:
            observe_nodes()
            observe_public_probes(failures)
        except urllib.error.HTTPError as exc:
            print(f"cluster watcher request failed: HTTP {exc.code}", flush=True)
        except Exception as exc:
            print(f"cluster watcher cycle failed: {type(exc).__name__}", flush=True)
        time.sleep(int(os.environ.get("SECRETARY_WATCH_INTERVAL_SECONDS", "30")))


if __name__ == "__main__":
    main()
