#!/usr/bin/env python3
"""Minimal authenticated HTTP facade for MobileService."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import Identity, MobileService, canonical_json, project_kubernetes_catalog


def load_key(path: str) -> Ed25519PrivateKey:
    raw = Path(path).read_bytes().strip()
    try:
        raw = base64.b64decode(raw, validate=True)
    except ValueError:
        pass
    return Ed25519PrivateKey.from_private_bytes(raw)


def kube_get(path: str) -> dict:
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    request = urllib.request.Request(f"https://{host}:{port}{path}", headers={"Authorization": f"Bearer {token}"})
    import ssl
    with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=ca), timeout=5) as response:
        return json.load(response)


def catalog() -> list[dict]:
    if os.getenv("KUBERNETES_CATALOG", "false").lower() != "true":
        return json.loads(Path(os.environ["CATALOG_PATH"]).read_text()).get("egresses", [])
    nodes = kube_get("/api/v1/nodes").get("items", [])
    pods = kube_get("/api/v1/namespaces/egress-fabric/pods?labelSelector=app.kubernetes.io%2Fname%3Dmihomos-mobile-publication").get("items", [])
    fabric = kube_get("/apis/networking.re8ch.com/v1alpha1/advancedfabrics/re8ch")
    return project_kubernetes_catalog(nodes, fabric, pods, int(os.getenv("MOBILE_SOCKS_PORT", "31081")))


def socks_credentials() -> tuple[str, str]:
    return (Path(os.environ["SOCKS_USERNAME_PATH"]).read_text().strip(),
            Path(os.environ["SOCKS_PASSWORD_PATH"]).read_text().strip())


SERVICE = MobileService(
    catalog,
    load_key(os.environ["SIGNING_KEY_PATH"]),
    Path(os.environ["CREDENTIAL_SECRET_PATH"]).read_bytes().strip(),
    socks_credentials,
    os.getenv("SIGNING_KEY_ID", "mobile-egress-2026-01"),
)


def authenticate(value: str) -> Identity:
    if not value.startswith("Bearer "):
        raise PermissionError("bearer token required")
    req = urllib.request.Request(os.getenv("AUTH_ME_URL", "https://api.re8ch.com/auth/me"),
                                 headers={"Authorization": value})
    with urllib.request.urlopen(req, timeout=5) as response:
        body = json.load(response)
    subject = body.get("sub") or body.get("id") or body.get("email")
    if not subject:
        raise PermissionError("identity has no stable subject")
    return Identity(str(subject), str(body.get("email", "")))


class Handler(BaseHTTPRequestHandler):
    def json(self, status: int, value: object, etag: str | None = None) -> None:
        raw = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if etag:
            self.send_header("ETag", f'"{etag}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        return json.loads(self.rfile.read(length) or b"{}")

    def identity(self) -> Identity:
        return authenticate(self.headers.get("Authorization", ""))

    def do_GET(self) -> None:
        try:
            if self.path == "/healthz":
                return self.json(200, {"ok": True})
            self.identity()
            device = self.headers.get("X-RE8CH-Device-ID", "")
            if self.path == "/v1/mobile/bootstrap":
                return self.json(200, SERVICE.bootstrap(device))
            if self.path == "/v1/mobile/egresses":
                value, etag = SERVICE.egresses()
                if self.headers.get("If-None-Match", "").strip('"') == etag:
                    self.send_response(304); self.end_headers(); return
                return self.json(200, value, etag)
            return self.json(404, {"error": "not found"})
        except PermissionError as exc:
            self.json(401, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def do_POST(self) -> None:
        try:
            identity = self.identity()
            body = self.body()
            device = self.headers.get("X-RE8CH-Device-ID", "")
            if self.path == "/v1/mobile/devices/enroll":
                return self.json(200, SERVICE.enroll(identity, body))
            if self.path == "/v1/mobile/credentials":
                return self.json(200, SERVICE.credentials(identity, device, str(body.get("egressID", ""))))
            if self.path == "/v1/mobile/telemetry/batches":
                return self.json(202, SERVICE.accept_telemetry(device, body))
            return self.json(404, {"error": "not found"})
        except PermissionError as exc:
            self.json(401, {"error": str(exc)})
        except (ValueError, LookupError) as exc:
            self.json(400, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("HTTP_PORT", "8080"))), Handler).serve_forever()
