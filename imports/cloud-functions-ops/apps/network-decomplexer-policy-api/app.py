#!/usr/bin/env python3
"""Stateless signed policy publisher for Network Decomplexer clients."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PORT = int(os.getenv("HTTP_PORT", "8080"))
AUTH_ME_URL = os.getenv("AUTH_ME_URL", "https://api.re8ch.com/auth/me")
MANAGEMENT_URL = os.getenv("MANAGEMENT_URL", "https://access.re8ch.com:8443")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://agent.re8ch.com:8443")
GEODB_PATH = Path(os.getenv("GEODB_PATH", "/data/geodb.json.gz"))
PATHS_PATH = Path(os.getenv("PATHS_PATH", "/secrets/paths.json"))
SIGNING_KEY_PATH = Path(os.getenv("SIGNING_KEY_PATH", "/secrets/ed25519-private-key"))
KEY_ID = os.getenv("SIGNING_KEY_ID", "network-decomplexer-2026-01")
MANIFEST_TTL_SECONDS = int(os.getenv("MANIFEST_TTL_SECONDS", "86400"))
AUTH_CACHE_SECONDS = int(os.getenv("AUTH_CACHE_SECONDS", "60"))


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()


def load_signing_key(path: Path) -> Ed25519PrivateKey:
    raw = path.read_bytes().strip()
    try:
        decoded = base64.b64decode(raw, validate=True)
    except ValueError:
        decoded = raw
    if len(decoded) != 32:
        raise ValueError("Ed25519 private key must contain exactly 32 raw bytes")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def signed_envelope(payload: dict, key: Ed25519PrivateKey, key_id: str = KEY_ID) -> dict:
    raw = canonical_json(payload)
    return {
        "algorithm": "Ed25519",
        "keyID": key_id,
        "payload": base64.b64encode(raw).decode(),
        "signature": base64.b64encode(key.sign(raw)).decode(),
    }


class AuthVerifier:
    def __init__(self) -> None:
        self.cache: dict[str, tuple[float, dict]] = {}

    def verify(self, authorization: str) -> dict:
        if not authorization.startswith("Bearer "):
            raise PermissionError("bearer token required")
        digest = hashlib.sha256(authorization.encode()).hexdigest()
        cached = self.cache.get(digest)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        request = urllib.request.Request(AUTH_ME_URL, headers={"Authorization": authorization})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                identity = json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise PermissionError("identity verification failed") from exc
        if not identity.get("id") and not identity.get("sub") and not identity.get("email"):
            raise PermissionError("identity response has no stable subject")
        self.cache[digest] = (time.monotonic() + AUTH_CACHE_SECONDS, identity)
        return identity


class PolicyStore:
    def __init__(self) -> None:
        self.key = load_signing_key(SIGNING_KEY_PATH)

    def artifact(self) -> tuple[bytes, str]:
        raw = GEODB_PATH.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()

    def paths(self) -> list[dict]:
        data = json.loads(PATHS_PATH.read_text())
        paths = data.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("paths.json paths must be an array")
        return paths

    def manifest(self) -> dict:
        artifact, digest = self.artifact()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {
            "schemaVersion": "re8ch.mobile-policy/v1",
            "version": f"{digest[:12]}-{int(now.timestamp())}",
            "issuedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": (now + timedelta(seconds=MANIFEST_TTL_SECONDS)).isoformat().replace("+00:00", "Z"),
            "managementURL": MANAGEMENT_URL,
            "geoDB": {
                "url": f"{PUBLIC_BASE_URL}/v1/mobile/artifacts/{digest}",
                "sha256": digest,
                "byteCount": len(artifact),
            },
            "enterpriseCIDRs": ["192.0.2.225/10"],
            "enterpriseDomains": ["netbird.selfhosted"],
            "paths": self.paths(),
            "publicFailOpen": True,
        }
        return signed_envelope(payload, self.key)

    def hints(self) -> dict:
        paths = self.paths()
        return signed_envelope({
            "schemaVersion": "re8ch.path-hints/v1",
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "paths": [{"id": item["id"], "healthy": item.get("healthy", True), "score": item.get("score", 0)} for item in paths],
        }, self.key)


AUTH = AuthVerifier()
STORE: PolicyStore | None = None


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, value: object) -> None:
        raw = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authenticate(self) -> dict:
        return AUTH.verify(self.headers.get("Authorization", ""))

    def do_GET(self) -> None:
        try:
            if self.path == "/healthz":
                return self.send_json(200, {"ok": True})
            self.authenticate()
            assert STORE is not None
            if self.path == "/v1/mobile/manifest":
                return self.send_json(200, STORE.manifest())
            if self.path == "/v1/mobile/path-hints":
                return self.send_json(200, STORE.hints())
            if self.path.startswith("/v1/mobile/artifacts/"):
                raw, digest = STORE.artifact()
                if self.path.rsplit("/", 1)[-1] != digest:
                    return self.send_json(404, {"error": "artifact not found"})
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Cache-Control", "private, max-age=86400, immutable")
                self.send_header("ETag", f'"{digest}"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                return self.wfile.write(raw)
            self.send_json(404, {"error": "not found"})
        except PermissionError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(503, {"error": type(exc).__name__})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    global STORE
    STORE = PolicyStore()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
