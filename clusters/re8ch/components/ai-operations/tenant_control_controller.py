#!/usr/bin/env python3
"""Tenant identity resolver and CRD status controller.

Kubernetes CRDs are authoritative. This service never accepts a client supplied
tenant and never returns the OIDC subject. Runtime callers send its SHA-256 hash
over a cluster-internal authenticated channel.
"""

import calendar
import hashlib
import hmac
import json
import os
import ssl
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GROUP = "tenancy.re8ch.com"
VERSION = "v1alpha1"
PORT = int(os.getenv("LISTEN_PORT", "19447"))
NAMESPACE = os.getenv("POD_NAMESPACE", "tenant-infrastructure")
IDENTITY = os.getenv("POD_NAME", socket.gethostname())
LEASE_NAME = "tenant-control-controller"


class Kubernetes:
    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base = f"https://{host}:{port}"
        self.token = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
        self.ssl = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")

    def request(self, method, path, body=None, content_type="application/json"):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json", "Content-Type": content_type},
        )
        with urllib.request.urlopen(req, context=self.ssl, timeout=5) as response:
            return json.load(response)

    def list(self, plural):
        return self.request("GET", f"/apis/{GROUP}/{VERSION}/{plural}").get("items", [])

    def patch_status(self, plural, name, status):
        return self.request("PATCH", f"/apis/{GROUP}/{VERSION}/{plural}/{name}/status", {"status": status}, "application/merge-patch+json")

    def claim_lease(self):
        path = f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases/{LEASE_NAME}"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            lease = self.request("GET", path)
            holder = lease.get("spec", {}).get("holderIdentity")
            renew = lease.get("spec", {}).get("renewTime", "1970-01-01T00:00:00Z")
            renewed = calendar.timegm(time.strptime(renew[:19] + "Z", "%Y-%m-%dT%H:%M:%SZ"))
            if holder not in (None, IDENTITY) and time.time() - renewed < 20:
                return False
            body = {"metadata": {"resourceVersion": lease["metadata"]["resourceVersion"]}, "spec": {"holderIdentity": IDENTITY, "leaseDurationSeconds": 20, "renewTime": now}}
            self.request("PUT", path, body)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return False
            self.request("POST", f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases", {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {"name": LEASE_NAME, "namespace": NAMESPACE}, "spec": {"holderIdentity": IDENTITY, "leaseDurationSeconds": 20, "acquireTime": now, "renewTime": now}})
        return True


KUBE = None


def kube():
    if KUBE is None:
        raise RuntimeError("Kubernetes client is not initialized")
    return KUBE


def condition(kind, ok, reason, message=""):
    return {"type": kind, "status": "True" if ok else "False", "reason": reason,
            "message": message[:1024], "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}


def resolve(issuer, subject_hash, groups=None):
    matches = []
    groups = {str(group) for group in (groups or []) if isinstance(group, str)}
    for binding in kube().list("tenantidentitybindings"):
        spec = binding.get("spec", {})
        if spec.get("revoked") or spec.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
            continue
        subject_match = bool(subject_hash and spec.get("subjectHash") and hmac.compare_digest(spec["subjectHash"], subject_hash))
        group_match = bool(spec.get("group") and spec["group"] in groups)
        if subject_match or group_match:
            matches.append((spec.get("tenantRef"), spec.get("role", "viewer")))
    tenants_matched = sorted(set(tenant for tenant, _ in matches if tenant))
    if len(tenants_matched) != 1:
        raise PermissionError("identity must resolve to exactly one active tenant")
    tenants = {item["metadata"]["name"]: item for item in kube().list("tenants")}
    tenant = tenants.get(tenants_matched[0])
    if not tenant or tenant.get("spec", {}).get("lifecycle") != "Active":
        raise PermissionError("tenant is not active")
    role = max((role for tenant_ref, role in matches if tenant_ref == tenants_matched[0]), key=lambda value: ROLE_ORDER.get(value, 0))
    if role not in ROLE_ORDER:
        raise PermissionError("identity binding has an invalid role")
    return {"tenantRef": tenants_matched[0], "role": role}


def reconcile_loop():
    while True:
        try:
            if not kube().claim_lease():
                time.sleep(5)
                continue
            for plural in ("tenants", "tenantidentitybindings", "tenantgrants"):
                for item in kube().list(plural):
                    meta = item["metadata"]
                    status = {"observedGeneration": meta.get("generation", 1), "operationId": "op_" + hashlib.sha256(meta["uid"].encode()).hexdigest()[:24],
                              "conditions": [condition("Ready", True, "DeclarationAccepted")]}
                    if item.get("status", {}).get("observedGeneration") != status["observedGeneration"]:
                        kube().patch_status(plural, meta["name"], status)
        except Exception:
            pass
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/livez": self.send_json(200, {"status": "ok"}); return
        if self.path == "/readyz":
            try: kube().list("tenants"); self.send_json(200, {"status": "ready"})
            except Exception: self.send_json(503, {"status": "not-ready"})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/v1/resolve": self.send_json(404, {"error": "not_found"}); return
        if self.headers.get("X-Tenant-Caller", "") not in {"tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc", "tenant-artchais"}:
            self.send_json(401, {"error": "unauthorized"}); return
        try:
            size = min(int(self.headers.get("Content-Length", "0")), 4096)
            body = json.loads(self.rfile.read(size))
            issuer = str(body["issuer"])
            subject_hash = str(body.get("subjectHash") or "")
            groups = body.get("groups") or []
            if subject_hash and not subject_hash.startswith("sha256:"): raise ValueError()
            if not isinstance(groups, list) or len(groups) > 128 or any(not isinstance(group, str) or len(group) > 256 for group in groups): raise ValueError()
            if not subject_hash and not groups: raise ValueError()
            self.send_json(200, resolve(issuer, subject_hash, groups))
        except PermissionError as exc: self.send_json(403, {"error": str(exc)})
        except Exception: self.send_json(400, {"error": "invalid_request"})

    def log_message(self, *_): pass


if __name__ == "__main__":
    KUBE = Kubernetes()
    threading.Thread(target=reconcile_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
