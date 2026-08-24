#!/usr/bin/env python3
"""Read-only K3S event collector for the Observable service.

The collector intentionally never mutates Kubernetes. It reads cluster state
through the in-cluster service account and writes normalized facts through the
public PostgREST observable service.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


KUBE_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
KUBE_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_API = "https://%s:%s" % (
    os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
    os.getenv("KUBERNETES_SERVICE_PORT", "443"),
)

DEFAULT_RW_BASE = os.getenv("OBSERVABLE_RPC_RW_BASE_URL", "https://db-rw.re8ch.com/observable").rstrip("/")
JWT_SECRET = os.getenv("PGRST_JWT_SECRET", "")
JWT_ROLE = os.getenv("OBSERVABLE_SERVICE_ROLE", "observable_app_rw")
JWT_AUD = os.getenv("PGRST_JWT_AUD", DEFAULT_RW_BASE)
JWT_ISS = os.getenv("OBSERVABLE_JWT_ISS", "observable-collector")
TENANT_ALIASES = os.getenv("OBSERVABLE_TENANT_ALIASES", "qwen=qianwen")
TENANT_CATALOG = os.getenv("OBSERVABLE_TENANT_CATALOG", "")
INTERVAL_SECONDS = int(os.getenv("OBSERVABLE_COLLECT_INTERVAL_SECONDS", "60"))
REQUEST_TIMEOUT = int(os.getenv("OBSERVABLE_REQUEST_TIMEOUT_SECONDS", "12"))
POD_RESTART_THRESHOLD = int(os.getenv("OBSERVABLE_POD_RESTART_THRESHOLD", "5"))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(level: str, message: str, **fields: Any) -> None:
    payload = {"ts": now_iso(), "level": level, "message": message}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


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


def kube_request(path: str) -> dict[str, Any]:
    token = read_file(KUBE_TOKEN)
    req = urllib.request.Request(
        KUBE_API + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    context = ssl.create_default_context(cafile=KUBE_CA)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=context) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


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


def parse_aliases(raw: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            aliases[part] = part
            continue
        left, right = part.split("=", 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            aliases[left] = right
    return aliases


def parse_catalog_aliases(text: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    in_observability = False
    tenant_id = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- id:"):
            in_observability = False
            tenant_id = ""
            continue
        if line == "observability:":
            in_observability = True
            tenant_id = ""
            continue
        if line.endswith(":") and line != "observability:" and not line.startswith("-"):
            in_observability = False
            continue
        if not in_observability or ":" not in line:
            continue
        key, value = [part.strip().strip("'\"") for part in line.split(":", 1)]
        if key == "tenantId":
            tenant_id = value
        elif key == "namespace" and tenant_id and value:
            aliases[value] = tenant_id
    return aliases


def load_tenant_aliases() -> dict[str, str]:
    aliases = parse_aliases(TENANT_ALIASES)
    if TENANT_CATALOG and os.path.exists(TENANT_CATALOG):
        try:
            aliases.update(parse_catalog_aliases(read_file(TENANT_CATALOG)))
        except Exception as exc:
            log("warning", "failed to parse tenant catalog", path=TENANT_CATALOG, error=str(exc))
    return aliases


def object_meta(obj: dict[str, Any]) -> dict[str, Any]:
    return obj.get("metadata") or {}


def labels_for(obj: dict[str, Any]) -> dict[str, str]:
    return (object_meta(obj).get("labels") or {}) if isinstance(object_meta(obj).get("labels"), dict) else {}


class TenantResolver:
    def __init__(self, aliases: dict[str, str] | None = None):
        self.aliases = aliases or {}
        self.namespace_labels: dict[str, dict[str, str]] = {}

    def refresh(self) -> None:
        data = kube_request("/api/v1/namespaces")
        labels: dict[str, dict[str, str]] = {}
        for item in data.get("items", []):
            meta = object_meta(item)
            labels[meta.get("name", "")] = meta.get("labels", {}) or {}
        self.namespace_labels = labels

    def tenant_for_namespace(self, namespace: str | None) -> str:
        ns = namespace or ""
        ns_labels = self.namespace_labels.get(ns, {})
        tenant = ns_labels.get("saas.re8ch.com/tenant") or ns_labels.get("tenant") or ns
        return self.aliases.get(tenant, tenant) if tenant else "platform"

    def tenant_for_object(self, obj: dict[str, Any], namespace: str | None = None) -> str:
        labels = labels_for(obj)
        tenant = labels.get("saas.re8ch.com/tenant") or labels.get("tenant")
        if tenant:
            return self.aliases.get(tenant, tenant)
        return self.tenant_for_namespace(namespace or object_meta(obj).get("namespace"))


def stable_fingerprint(*parts: Any) -> str:
    raw = "|".join(json.dumps(part, sort_keys=True, ensure_ascii=False, default=str) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def observable_event(
    *,
    tenant_id: str,
    namespace: str,
    source: str,
    severity: str,
    reason: str,
    object_ref: str,
    message: str,
    status: str = "active",
    fingerprint: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = raw_payload or {}
    return {
        "tenant_id": tenant_id or "platform",
        "namespace": namespace or "",
        "source": source,
        "severity": severity or "info",
        "reason": reason or "",
        "object_ref": object_ref or "",
        "message": message or "",
        "status": status or "active",
        "fingerprint": fingerprint or stable_fingerprint(source, reason, object_ref, message),
        "raw_payload": raw,
    }


def collect_k8s_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    data = kube_request("/api/v1/events")
    events: list[dict[str, Any]] = []
    for item in data.get("items", []):
        event_type = item.get("type") or "Normal"
        if event_type != "Warning" and os.getenv("OBSERVABLE_INCLUDE_NORMAL_EVENTS", "") != "1":
            continue
        meta = object_meta(item)
        namespace = meta.get("namespace", "")
        involved = item.get("involvedObject") or {}
        kind = involved.get("kind") or "Object"
        name = involved.get("name") or meta.get("name") or ""
        reason = item.get("reason") or event_type
        message = item.get("message") or ""
        object_ref = "%s/%s/%s" % (kind, namespace, name) if namespace else "%s/%s" % (kind, name)
        tenant_id = resolver.tenant_for_namespace(namespace)
        events.append(observable_event(
            tenant_id=tenant_id,
            namespace=namespace,
            source="k3s-event",
            severity="warning" if event_type == "Warning" else "info",
            reason=reason,
            object_ref=object_ref,
            message=message,
            fingerprint=stable_fingerprint("k3s-event", tenant_id, object_ref, reason),
            raw_payload=item,
        ))
    return events


def pod_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    data = kube_request("/api/v1/pods")
    events: list[dict[str, Any]] = []
    for pod in data.get("items", []):
        meta = object_meta(pod)
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        status = pod.get("status") or {}
        phase = status.get("phase", "")
        statuses = status.get("containerStatuses") or []
        restarts = sum(int(c.get("restartCount") or 0) for c in statuses)
        unready = [c.get("name") for c in statuses if phase == "Running" and not c.get("ready")]
        reason = ""
        severity = "warning"
        message = ""
        if phase not in ("Running", "Succeeded"):
            reason = "pod_unhealthy"
            message = "Pod %s/%s phase=%s" % (namespace, name, phase)
        elif unready:
            reason = "pod_container_unready"
            message = "Pod %s/%s has unready containers: %s" % (namespace, name, ",".join(unready))
        elif restarts >= POD_RESTART_THRESHOLD:
            reason = "pod_restarts"
            message = "Pod %s/%s restart count is %d" % (namespace, name, restarts)
        if not reason:
            continue
        tenant_id = resolver.tenant_for_object(pod, namespace)
        events.append(observable_event(
            tenant_id=tenant_id,
            namespace=namespace,
            source="k3s-pod",
            severity=severity,
            reason=reason,
            object_ref="Pod/%s/%s" % (namespace, name),
            message=message,
            fingerprint=stable_fingerprint("pod", tenant_id, namespace, name, reason),
            raw_payload=pod,
        ))
    return events


def deployment_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    data = kube_request("/apis/apps/v1/deployments")
    events: list[dict[str, Any]] = []
    for deploy in data.get("items", []):
        meta = object_meta(deploy)
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        status = deploy.get("status") or {}
        desired = int((deploy.get("spec") or {}).get("replicas") or 0)
        available = int(status.get("availableReplicas") or 0)
        unavailable = int(status.get("unavailableReplicas") or max(desired - available, 0))
        if desired == 0 or unavailable <= 0:
            continue
        tenant_id = resolver.tenant_for_object(deploy, namespace)
        events.append(observable_event(
            tenant_id=tenant_id,
            namespace=namespace,
            source="k3s-deployment",
            severity="warning",
            reason="deployment_unavailable",
            object_ref="Deployment/%s/%s" % (namespace, name),
            message="Deployment %s/%s has %d unavailable replica(s)" % (namespace, name, unavailable),
            fingerprint=stable_fingerprint("deployment", tenant_id, namespace, name, "unavailable"),
            raw_payload=deploy,
        ))
    return events


def job_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    data = kube_request("/apis/batch/v1/jobs")
    events: list[dict[str, Any]] = []
    for job in data.get("items", []):
        meta = object_meta(job)
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        conditions = (job.get("status") or {}).get("conditions") or []
        failed = next((c for c in conditions if c.get("type") == "Failed" and c.get("status") == "True"), None)
        if not failed:
            continue
        tenant_id = resolver.tenant_for_object(job, namespace)
        reason = failed.get("reason") or "job_failed"
        message = failed.get("message") or "Job %s/%s failed" % (namespace, name)
        events.append(observable_event(
            tenant_id=tenant_id,
            namespace=namespace,
            source="k3s-job",
            severity="warning",
            reason=reason,
            object_ref="Job/%s/%s" % (namespace, name),
            message=message,
            fingerprint=stable_fingerprint("job", tenant_id, namespace, name, reason),
            raw_payload=job,
        ))
    return events


def hpa_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    data = kube_request("/apis/autoscaling/v2/horizontalpodautoscalers")
    events: list[dict[str, Any]] = []
    for hpa in data.get("items", []):
        meta = object_meta(hpa)
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        bad_conditions = [
            c for c in (hpa.get("status") or {}).get("conditions", [])
            if c.get("status") == "False" and c.get("type") in ("AbleToScale", "ScalingActive")
        ]
        for cond in bad_conditions:
            tenant_id = resolver.tenant_for_object(hpa, namespace)
            reason = cond.get("reason") or "hpa_condition_false"
            message = cond.get("message") or "HPA %s/%s condition %s is false" % (namespace, name, cond.get("type"))
            events.append(observable_event(
                tenant_id=tenant_id,
                namespace=namespace,
                source="k3s-hpa",
                severity="warning",
                reason=reason,
                object_ref="HorizontalPodAutoscaler/%s/%s" % (namespace, name),
                message=message,
                fingerprint=stable_fingerprint("hpa", tenant_id, namespace, name, cond.get("type"), reason),
                raw_payload=hpa,
            ))
    return events


def certificate_events(resolver: TenantResolver) -> list[dict[str, Any]]:
    try:
        data = kube_request("/apis/cert-manager.io/v1/certificates")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    events: list[dict[str, Any]] = []
    for cert in data.get("items", []):
        meta = object_meta(cert)
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        ready = next((c for c in (cert.get("status") or {}).get("conditions", []) if c.get("type") == "Ready"), None)
        if not ready or ready.get("status") == "True":
            continue
        tenant_id = resolver.tenant_for_object(cert, namespace)
        reason = ready.get("reason") or "certificate_not_ready"
        message = ready.get("message") or "Certificate %s/%s is not ready" % (namespace, name)
        events.append(observable_event(
            tenant_id=tenant_id,
            namespace=namespace,
            source="k3s-certificate",
            severity="warning",
            reason=reason,
            object_ref="Certificate/%s/%s" % (namespace, name),
            message=message,
            fingerprint=stable_fingerprint("certificate", tenant_id, namespace, name, reason),
            raw_payload=cert,
        ))
    return events


def node_events() -> list[dict[str, Any]]:
    data = kube_request("/api/v1/nodes")
    events: list[dict[str, Any]] = []
    for node in data.get("items", []):
        name = object_meta(node).get("name", "")
        for cond in (node.get("status") or {}).get("conditions", []):
            ctype = cond.get("type")
            status = cond.get("status")
            bad = (ctype == "Ready" and status != "True") or (ctype in ("MemoryPressure", "DiskPressure", "PIDPressure") and status == "True")
            if not bad:
                continue
            reason = cond.get("reason") or "node_condition"
            message = cond.get("message") or "Node %s condition %s=%s" % (name, ctype, status)
            events.append(observable_event(
                tenant_id="platform",
                namespace="",
                source="k3s-node",
                severity="critical" if ctype == "Ready" else "warning",
                reason=reason,
                object_ref="Node/%s" % name,
                message=message,
                fingerprint=stable_fingerprint("node", name, ctype, reason),
                raw_payload={"metadata": object_meta(node), "condition": cond},
            ))
    return events


COLLECTORS = (
    collect_k8s_events,
    pod_events,
    deployment_events,
    job_events,
    hpa_events,
    certificate_events,
)


def collect_once(resolver: TenantResolver) -> list[dict[str, Any]]:
    resolver.refresh()
    events: list[dict[str, Any]] = []
    for collector in COLLECTORS:
        try:
            events.extend(collector(resolver))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log("info", "collector API not installed", collector=collector.__name__)
                continue
            raise
    events.extend(node_events())
    return events


def ingest(events: list[dict[str, Any]], rw_base: str = DEFAULT_RW_BASE) -> Any:
    if not events:
        return {"ok": True, "count": 0}
    url = rw_base.rstrip("/") + "/rpc/service_observable_event_ingest"
    return http_json("POST", url, {"p_events": events}, headers={"Authorization": "Bearer " + service_jwt()})


def run_once(dry_run: bool = False) -> int:
    resolver = TenantResolver(load_tenant_aliases())
    events = collect_once(resolver)
    if dry_run:
        print(json.dumps({"events": events, "count": len(events)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    result = ingest(events)
    log("info", "observable collection complete", collected=len(events), result=result)
    return 0


def main() -> int:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    while True:
        try:
            run_once(dry_run=dry_run)
        except Exception as exc:
            log("error", "observable collection failed", error=str(exc), errorType=type(exc).__name__)
        if once:
            return 0
        time.sleep(max(INTERVAL_SECONDS, 10))


if __name__ == "__main__":
    raise SystemExit(main())
