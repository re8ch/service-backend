#!/usr/bin/env python3
"""Dex-fronted device portal and standard proxy subscription endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import DEFAULT_NODES, canonical_subject, issue_token, proxy_password, subscription_body, subscription_headers, verify_token


NAMESPACE = os.getenv("POD_NAMESPACE", "egress-fabric")
STATE_NAME = os.getenv("STATE_CONFIGMAP", "access-subscription-devices")
USERS_SECRET = os.getenv("USERS_SECRET", "personal-access-users")
HMAC_SECRET = Path(os.getenv("HMAC_SECRET_PATH", "/secrets/hmac-key")).read_bytes().strip()
IDENTITY_ALIASES = json.loads(os.getenv("IDENTITY_ALIASES_JSON", "{}"))


PORTAL = """<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>access re8ch com</title><style>body{font:16px system-ui;margin:0;background:#101114;color:#eee}main{max-width:720px;margin:auto;padding:32px}button,input{font:inherit;padding:10px;margin:4px;border-radius:8px}article{background:#1d1f24;padding:16px;margin:12px 0;border-radius:12px}code{overflow-wrap:anywhere;color:#9ee6a8}</style>
<main><h1>access re8ch com</h1><p>AnyTLS 主线路 · Hysteria2 备用 · 每月 300 GB 软提醒</p><p id=a></p><form id=f><input id=n maxlength=64 placeholder='设备名称，例如 iPhone'><button>创建设备</button></form><div id=o></div><p>订阅地址只显示一次。复制到 Shadowrocket 后请妥善保存。</p></main>
<script>const o=document.querySelector('#o'),a=document.querySelector('#a');async function load(){let r=await fetch('/api/devices'),j=await r.json();a.textContent=j.account?'账号：'+j.account:'';o.innerHTML=(j.devices||[]).map(x=>`<article><b>${esc(x.name)}</b> · ${x.enabled?'启用':'已撤销'} <button onclick="revoke('${x.id}')">撤销</button></article>`).join('')||'<article>尚无设备</article>'}function esc(s){let d=document.createElement('div');d.textContent=s;return d.innerHTML}async function revoke(id){await fetch('/api/devices/'+id,{method:'DELETE'});load()}f.onsubmit=async e=>{e.preventDefault();let r=await fetch('/api/devices',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:n.value||'device'})}),j=await r.json();if(j.subscriptionURL)o.innerHTML=`<article><b>请立即复制：</b><br><code>${esc(j.subscriptionURL)}</code></article>`+o.innerHTML;else alert(j.error||'创建失败')};load()</script></html>"""


def kube_request(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"https://{host}:{port}{path}", data=raw, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"},
    )
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.load(response)


def state() -> dict:
    item = kube_request("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}")
    return json.loads(item.get("data", {}).get("devices.json", "{}"))


def save(devices: dict) -> None:
    kube_request("PATCH", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}",
                 {"data": {"devices.json": json.dumps(devices, separators=(",", ":"), sort_keys=True)}})
    users = []
    for item in devices.values():
        if item.get("enabled"):
            users.append(f"          {item['jti']}: {proxy_password(HMAC_SECRET, item['jti'])}")
    if not users:
        users.append("          disabled-bootstrap: disabled")
    kube_request("PATCH", f"/api/v1/namespaces/{NAMESPACE}/secrets/{USERS_SECRET}",
                 {"stringData": {"users.yaml": "\n".join(users) + "\n"}})
    for deployment in ("personal-access-us-ca", "personal-access-my-kul"):
        kube_request("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{deployment}",
                     {"spec": {"template": {"metadata": {"annotations": {
                         "access.re8ch.com/users-revision": hashlib.sha256(json.dumps(devices, sort_keys=True).encode()).hexdigest()
                     }}}}})


def identity(headers) -> str:
    subject = headers.get("X-Auth-Request-User") or headers.get("X-Forwarded-User")
    email = headers.get("X-Auth-Request-Email") or headers.get("X-Forwarded-Email")
    try:
        return canonical_subject(subject, email, IDENTITY_ALIASES)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc


def owned_by(record: dict, user: str) -> bool:
    try:
        return canonical_subject(record.get("subject"), None, IDENTITY_ALIASES) == user
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, raw: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def json(self, status: int, value: object) -> None:
        self.send(status, (json.dumps(value, separators=(",", ":")) + "\n").encode(), "application/json")

    def body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        try:
            if self.path == "/healthz":
                return self.json(200, {"ok": True})
            if self.path.startswith("/s/"):
                token = self.path.split("?", 1)[0].removeprefix("/s/")
                payload = verify_token(HMAC_SECRET, token)
                record = state().get(payload["jti"])
                if not record or not record.get("enabled") or record.get("tokenHash") != hashlib.sha256(token.encode()).hexdigest():
                    raise PermissionError("subscription revoked")
                password = proxy_password(HMAC_SECRET, payload["jti"])
                return self.send(200, subscription_body(password), "text/plain; charset=utf-8", subscription_headers())
            user = identity(self.headers)
            records = [
                {"id": key, "name": value.get("name", "device"), "enabled": bool(value.get("enabled")),
                 "expiresAt": value.get("expiresAt")}
                for key, value in state().items() if owned_by(value, user)
            ]
            if self.path.split("?", 1)[0] == "/api/devices":
                return self.json(200, {"profile": "access re8ch com", "account": user, "devices": records})
            if self.path.split("?", 1)[0] == "/api/status":
                return self.json(200, {"profile": "access re8ch com", "quota": "300 GB soft", "nodes": len(DEFAULT_NODES)})
            if self.path.split("?", 1)[0] == "/":
                return self.send(200, PORTAL.encode(), "text/html; charset=utf-8")
            return self.json(404, {"error": "not found"})
        except (PermissionError, ValueError) as exc:
            self.json(401, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def do_POST(self) -> None:
        try:
            user = identity(self.headers)
            path = self.path.split("?", 1)[0]
            if path != "/api/devices" and not (path.startswith("/api/devices/") and path.endswith("/rotate")):
                return self.json(404, {"error": "not found"})
            body = self.body()
            devices = state()
            if path.endswith("/rotate"):
                old_id = path.removeprefix("/api/devices/").removesuffix("/rotate").strip("/")
                old = devices.get(old_id)
                if not old or not owned_by(old, user):
                    raise PermissionError("device not found")
                old["enabled"] = False
                name = old.get("name", "device")
            else:
                name = str(body.get("name", "device")).strip()[:64] or "device"
            token = issue_token(HMAC_SECRET, user, name)
            payload = verify_token(HMAC_SECRET, token)
            devices[payload["jti"]] = {
                "subject": user, "name": name, "jti": payload["jti"], "enabled": True,
                "expiresAt": payload["exp"], "tokenHash": hashlib.sha256(token.encode()).hexdigest(),
            }
            save(devices)
            return self.json(201, {"id": payload["jti"], "subscriptionURL": f"https://sub.access.re8ch.com/s/{token}"})
        except (PermissionError, ValueError) as exc:
            self.json(400, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def do_DELETE(self) -> None:
        try:
            user = identity(self.headers)
            if not self.path.startswith("/api/devices/"):
                return self.json(404, {"error": "not found"})
            device_id = self.path.removeprefix("/api/devices/").split("/", 1)[0]
            devices = state()
            record = devices.get(device_id)
            if not record or not owned_by(record, user):
                raise PermissionError("device not found")
            record["enabled"] = False
            save(devices)
            self.send(204, b"", "application/json")
        except PermissionError as exc:
            self.json(404, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("HTTP_PORT", "8080"))), Handler).serve_forever()
