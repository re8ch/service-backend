#!/usr/bin/env python3
"""GitHub collaborator reconciler and AnyTLS/Hysteria2 credential controller."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from core import AUTH_MAX_STALE_SECONDS, canonical_subject, proxy_password

NAMESPACE = os.getenv("POD_NAMESPACE", "egress-fabric")
STATE_NAME = os.getenv("STATE_CONFIGMAP", "access-subscription-devices")
AUTH_NAME = os.getenv("AUTH_CONFIGMAP", "access-members-cache")
USERS_SECRET = os.getenv("USERS_SECRET", "personal-access-users")
DEPLOYMENTS = tuple(filter(None, os.getenv("ACCESS_DEPLOYMENTS", "personal-access-us-ca,personal-access-my-kul,personal-access-us-la,personal-access-cn-loudi").split(",")))
ALIASES = json.loads(os.getenv("IDENTITY_ALIASES_JSON", "{}"))
APP_ID = os.getenv("GITHUB_APP_ID", "")
INSTALLATION_ID = os.getenv("GITHUB_INSTALLATION_ID", "")
REPOSITORY = os.getenv("GITHUB_REPOSITORY", "re8ch/access-edge-members")
PRIVATE_KEY_PATH = Path(os.getenv("GITHUB_PRIVATE_KEY_PATH", "/github-app/private-key.pem"))
HMAC_SECRET_PATH = Path(os.getenv("HMAC_SECRET_PATH", "/secrets/hmac-key"))


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    tag = data[offset]
    length = data[offset + 1]
    used = 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset + 2:offset + 2 + count], "big")
        used += count
    start = offset + used
    return tag, data[start:start + length], start + length


def _children(sequence: bytes) -> list[tuple[int, bytes]]:
    result, offset = [], 0
    while offset < len(sequence):
        tag, value, offset = _tlv(sequence, offset)
        result.append((tag, value))
    return result


def rsa_private_numbers(pem: bytes) -> tuple[int, int]:
    body = b"".join(line.strip() for line in pem.splitlines() if not line.startswith(b"---"))
    der = base64.b64decode(body)
    tag, sequence, _ = _tlv(der)
    if tag != 0x30:
        raise ValueError("invalid RSA private key")
    items = _children(sequence)
    if len(items) >= 3 and items[1][0] == 0x30 and items[2][0] == 0x04:  # PKCS#8
        tag, sequence, _ = _tlv(items[2][1])
        if tag != 0x30:
            raise ValueError("invalid PKCS#8 RSA key")
        items = _children(sequence)
    if len(items) < 4 or any(items[index][0] != 0x02 for index in (1, 3)):
        raise ValueError("unsupported RSA private key")
    return int.from_bytes(items[1][1], "big"), int.from_bytes(items[3][1], "big")


def github_jwt(app_id: str, pem: bytes, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = b64url(json.dumps({"iat": issued - 30, "exp": issued + 540, "iss": app_id}, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing).digest()
    modulus, private_exponent = rsa_private_numbers(pem)
    size = (modulus.bit_length() + 7) // 8
    encoded = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), private_exponent, modulus).to_bytes(size, "big")
    return f"{header}.{payload}.{b64url(signature)}"


def api_request(url: str, token: str, method: str = "GET", body: dict | None = None, etag: str = ""):
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
               "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "re8ch-access-controller"}
    if etag:
        headers["If-None-Match"] = etag
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=raw, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers, json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, exc.headers, None
        raise


def installation_token() -> str:
    jwt = github_jwt(APP_ID, PRIVATE_KEY_PATH.read_bytes())
    _status, _headers, data = api_request(f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens", jwt, "POST", {})
    return data["token"]


def permission(item: dict) -> str:
    role = str(item.get("role_name", "")).casefold()
    if role in {"read", "pull", "triage", "write", "push", "maintain", "admin"}:
        return role
    permissions = item.get("permissions", {})
    for candidate in ("admin", "maintain", "push", "triage", "pull"):
        if permissions.get(candidate):
            return {"push": "write", "pull": "read"}.get(candidate, candidate)
    return "none"


def canonical_members(items: list[dict], aliases: dict[str, str] = ALIASES) -> dict:
    members = {}
    for item in items:
        login = str(item.get("login", "")).casefold()
        role = permission(item)
        if role not in {"read", "pull", "triage", "write", "push", "maintain", "admin"}:
            continue
        subject = canonical_subject(login, None, aliases)
        members[subject] = {"authorized": True, "admin": role in {"maintain", "admin"}, "github": login, "permission": role}
    return members


def kube_request(method: str, path: str, body: dict | None = None, content_type: str = "application/json") -> dict:
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"https://{host}:{port}{path}", data=raw, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        return json.load(response) if response.length != 0 else {}


def object_get(resource: str, name: str) -> dict:
    return kube_request("GET", f"/api/v1/namespaces/{NAMESPACE}/{resource}/{name}")


def replace_configmap(item: dict, key: str, value: dict) -> None:
    item["data"] = {**item.get("data", {}), key: json.dumps(value, separators=(",", ":"), sort_keys=True)}
    item.pop("status", None)
    kube_request("PUT", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{item['metadata']['name']}", item)


def sync_members(previous: dict) -> dict:
    token = installation_token()
    status, headers, data = api_request(f"https://api.github.com/repos/{REPOSITORY}/collaborators?affiliation=all&per_page=100", token, etag=previous.get("etag", ""))
    now = int(time.time())
    if status == 304:
        return {**previous, "lastSuccess": now}
    return {"repository": REPOSITORY, "lastSuccess": now, "etag": headers.get("ETag", ""), "members": canonical_members(data)}


def has_users_revision(deployment: dict, revision: str) -> bool:
    annotations = deployment.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}) or {}
    return annotations.get("access.re8ch.com/users-revision") == revision


def reconcile() -> None:
    auth_item = object_get("configmaps", AUTH_NAME)
    previous = json.loads(auth_item.get("data", {}).get("members.json", "{}"))
    try:
        current = sync_members(previous)
        replace_configmap(auth_item, "members.json", current)
    except Exception as exc:
        current = previous
        print(json.dumps({"level": "warning", "event": "github-sync-failed", "error": type(exc).__name__}), flush=True)
    now = int(time.time())
    members = current.get("members", {}) if now - int(current.get("lastSuccess", 0) or 0) <= AUTH_MAX_STALE_SECONDS else {}
    devices_item = object_get("configmaps", STATE_NAME)
    devices = json.loads(devices_item.get("data", {}).get("devices.json", "{}"))
    changed = False
    for record in devices.values():
        subject = canonical_subject(record.get("subject"), None, ALIASES)
        if record.get("enabled") and not members.get(subject, {}).get("authorized"):
            record["enabled"] = False
            record["revokedReason"] = "github-access-removed-or-stale"
            changed = True
    if changed:
        replace_configmap(devices_item, "devices.json", devices)
    hmac_secret = HMAC_SECRET_PATH.read_bytes().strip()
    users = [f"          {item['jti']}: {proxy_password(hmac_secret, item['jti'])}" for item in devices.values()
             if item.get("enabled") and members.get(canonical_subject(item.get("subject"), None, ALIASES), {}).get("authorized")]
    if not users:
        users = ["          disabled-bootstrap: disabled"]
    users_yaml = "\n".join(sorted(users)) + "\n"
    revision = hashlib.sha256(users_yaml.encode()).hexdigest()
    secret = object_get("secrets", USERS_SECRET)
    old_revision = secret.get("metadata", {}).get("annotations", {}).get("access.re8ch.com/users-revision")
    if old_revision != revision:
        patch = {"metadata": {"annotations": {"access.re8ch.com/users-revision": revision}}, "stringData": {"users.yaml": users_yaml}}
        kube_request("PATCH", f"/api/v1/namespaces/{NAMESPACE}/secrets/{USERS_SECRET}", patch, "application/merge-patch+json")
    for deployment in DEPLOYMENTS:
        current = kube_request("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{deployment}")
        if has_users_revision(current, revision):
            continue
        kube_request("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{deployment}",
                     {"spec": {"template": {"metadata": {"annotations": {"access.re8ch.com/users-revision": revision}}}}},
                     "application/merge-patch+json")


if __name__ == "__main__":
    while True:
        started = time.monotonic()
        try:
            reconcile()
        except Exception as exc:
            print(json.dumps({"level": "error", "event": "reconcile-failed", "error": type(exc).__name__}), flush=True)
        time.sleep(max(1, 60 - (time.monotonic() - started)))
