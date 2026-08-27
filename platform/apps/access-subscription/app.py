#!/usr/bin/env python3
"""Dex-fronted Store, admin API and device-scoped subscription service."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from core import (DEFAULT_NODES, authorization, canonical_subject, issue_token, mihomo_config, proxy_password,
                  public_node, shadowrocket_config, shadowrocket_import_url, subscription_body,
                  subscription_headers, verify_token)

NAMESPACE = os.getenv("POD_NAMESPACE", "egress-fabric")
STATE_NAME = os.getenv("STATE_CONFIGMAP", "access-subscription-devices")
AUTH_NAME = os.getenv("AUTH_CONFIGMAP", "access-members-cache")
USAGE_NAME = os.getenv("USAGE_CONFIGMAP", "access-device-usage")
RULES_ROOT = Path(os.getenv("RULES_ROOT", "/rules"))
BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://sub.access.re8ch.com").rstrip("/")
HMAC_SECRET = Path(os.getenv("HMAC_SECRET_PATH", "/secrets/hmac-key")).read_bytes().strip()
IDENTITY_ALIASES = json.loads(os.getenv("IDENTITY_ALIASES_JSON", "{}"))

PORTAL = """<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>access re8ch com</title><style>:root{color-scheme:dark}*{box-sizing:border-box}body{font:16px system-ui;margin:0;background:#101114;color:#eee}main{max-width:920px;margin:auto;padding:32px 20px}button,input,a.cta{font:inherit;padding:11px 16px;border-radius:9px;border:1px solid #3b414b}button{cursor:pointer}input{min-width:280px}a{color:#9ee6a8}.cta{display:inline-block;text-decoration:none;font-weight:650}.primary{background:#83dfa0;color:#102016;border-color:#83dfa0}.secondary{background:#292d35;color:#eee}.danger{color:#ff9c9c}article{background:#1d1f24;padding:20px;margin:14px 0;border-radius:14px}.choice-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.choice{background:#17191e;border:1px solid #353a44;border-radius:12px;padding:18px}.recommended{border-color:#72d993}.badge{display:inline-block;background:#244a31;color:#a8f0bd;padding:3px 8px;border-radius:99px;font-size:13px;font-weight:700}.muted{color:#aaa}.warning{color:#ffd28a}.row{display:flex;gap:8px;flex-wrap:wrap}.steps{padding-left:22px;line-height:1.65}code.url{display:block;margin-top:10px;padding:10px;background:#0f1114;border-radius:8px;overflow-wrap:anywhere;color:#9ee6a8}@media(max-width:700px){.choice-grid{grid-template-columns:1fr}main{padding:20px 12px}}</style>
<main><h1>access re8ch com</h1><p>AnyTLS 主线路 · Hysteria2 备用 · 每月 300 GB 软提醒</p><p id=a></p><form id=f><input id=n maxlength=64 placeholder='设备名称，例如 iPhone'><button>创建设备</button></form><div id=new></div><div id=o></div><p class=muted>设备地址只显示一次；Shadowrocket 一键导入会同时安装节点、策略组、分流规则和 DNS。</p></main>
<script>const o=document.querySelector('#o'),a=document.querySelector('#a'),nn=document.querySelector('#new');function esc(s){let d=document.createElement('div');d.textContent=s;return d.innerHTML}function size(n){return (n/1073741824).toFixed(2)+' GB'}function when(n){return n?new Date(n*1000).toLocaleString():'尚无'}async function load(){let r=await fetch('/api/me'),j=await r.json();if(!r.ok){a.textContent=j.error||'未授权';return}a.textContent='账号：'+j.account+(j.admin?' · 管理员':'');o.innerHTML=(j.devices||[]).map(x=>`<article><b>${esc(x.name)}</b> · ${x.enabled?'启用':'已撤销'}<br><span class=muted>${x.activeConnections||0} 个连接 · 本月 ${size((x.upload||0)+(x.download||0))} · 最后在线 ${when(x.lastSeenAt)}</span><div class=row><button onclick="rotate('${x.id}')">轮换并生成新配置</button><button class=secondary onclick="revoke('${x.id}')">撤销设备</button></div></article>`).join('')||'<article>还没有设备。输入设备名称并创建，页面会生成一次性导入地址。</article>'}async function revoke(id){await fetch('/api/devices/'+id,{method:'DELETE'});load()}async function rotate(id){let r=await fetch('/api/devices/'+id+'/rotate',{method:'POST'}),j=await r.json();show(j)}function show(j){if(!j.subscriptionURL)return alert(j.error||'创建失败');nn.innerHTML=`<article><h2>选择导入方式</h2><p class=warning>这些地址只显示一次。请在当前设备上立即完成导入。</p><div class=choice-grid><section class="choice recommended"><span class=badge>推荐</span><h3>完整配置</h3><p>包含节点、策略组、OpenAI/Google 分流规则和安全 DNS。它会替换 Shadowrocket 当前使用的 <code>default.conf</code>，适合 Mac、iPhone 的日常使用。</p><div class=row><a class="cta primary" href="${esc(j.shadowrocketImportURL)}">打开 Shadowrocket 安装</a><a class="cta secondary" href="${esc(j.shadowrocketURL)}" download="re8ch-shadowrocket.conf">下载 .conf 文件</a></div><h4>macOS 手动导入</h4><ol class=steps><li>打开 Shadowrocket 底部 <b>Config</b>。</li><li>在 <b>REMOTE FILES</b> 点 <b>Add Configuration</b>。</li><li>粘贴下方完整配置 URL 并下载。</li><li>点击新配置，选择 <b>Use Config</b>。</li><li>确认蓝色勾已从 <code>default.conf</code> 移到新配置。</li></ol><code class=url>${esc(j.shadowrocketURL)}</code></section><section class=choice><h3>仅节点订阅</h3><p>只添加或更新代理节点，不包含策略组、分流规则和 DNS，也不会替换 <code>default.conf</code>。仅用于已有自定义规则的高级用户。</p><a class="cta secondary" href="${esc(j.subscriptionURL)}">复制或打开节点订阅</a><hr><h3>Mihomo 完整配置</h3><p>供 Clash Meta、Mihomo 和兼容客户端使用，不适用于 Shadowrocket 的 Config 页面。</p><a class="cta secondary" href="${esc(j.mihomoURL)}">打开 Mihomo YAML</a></section></div></article>`}f.onsubmit=async e=>{e.preventDefault();let r=await fetch('/api/devices',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:n.value||'device'})}),j=await r.json();show(j);load()};load()</script></html>"""

ADMIN = """<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Access Admin</title><style>body{font:15px system-ui;background:#101114;color:#eee;margin:30px}article{background:#1d1f24;padding:14px;margin:10px;border-radius:10px}button{padding:7px}</style><h1>Access Admin</h1><div id=o></div><script>function esc(s){let d=document.createElement('div');d.textContent=String(s);return d.innerHTML}async function load(){let r=await fetch('/api/admin/users'),j=await r.json();o.innerHTML=Object.entries(j.members||{}).map(([u,m])=>`<article><b>${esc(u)}</b> · ${esc(m.permission||'')}${m.admin?' · admin':''}<div>${(j.devices[u]||[]).map(d=>`${esc(d.name)} · ${d.enabled?'启用':'撤销'} · ${d.activeConnections||0} 连接 <button onclick="revoke('${d.id}')">撤销</button>`).join('<br>')||'无设备'}</div></article>`).join('')}async function revoke(id){await fetch('/api/admin/devices/'+id,{method:'DELETE'});load()}load()</script></html>"""


def kube_request(method: str, path: str, body: dict | None = None, content_type: str = "application/json") -> dict:
    host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"https://{host}:{port}{path}", data=raw, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
    context = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.load(response) if response.length != 0 else {}


def configmap(name: str) -> tuple[dict, dict]:
    item = kube_request("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{name}")
    return item, item.get("data", {})


def json_state(name: str, key: str) -> dict:
    _item, data = configmap(name)
    return json.loads(data.get(key, "{}"))


def devices_state() -> dict:
    return json_state(STATE_NAME, "devices.json")


def auth_state() -> dict:
    return json_state(AUTH_NAME, "members.json")


def usage_state() -> dict:
    try:
        _item, data = configmap(USAGE_NAME)
        merged = {}
        for key, raw in data.items():
            if not key.startswith("usage-"):
                continue
            for device, stats in json.loads(raw).get("devices", {}).items():
                item = merged.setdefault(device, {"upload": 0, "download": 0, "activeConnections": 0})
                item["upload"] += int(stats.get("upload", 0))
                item["download"] += int(stats.get("download", 0))
                item["activeConnections"] += int(stats.get("activeConnections", 0))
                if int(stats.get("lastSeenAt", 0)) > int(item.get("lastSeenAt", 0) or 0):
                    item["lastSeenAt"] = stats.get("lastSeenAt")
        return merged
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def update_devices(mutator, attempts: int = 5) -> dict:
    for _ in range(attempts):
        item, data = configmap(STATE_NAME)
        devices = json.loads(data.get("devices.json", "{}"))
        result = mutator(devices)
        item["data"] = {**data, "devices.json": json.dumps(devices, separators=(",", ":"), sort_keys=True)}
        item.pop("status", None)
        try:
            kube_request("PUT", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_NAME}", item)
            return result
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
    raise RuntimeError("device state update conflict")


def identity(headers) -> str:
    preferred = headers.get("X-Auth-Request-Preferred-Username") or headers.get("X-Forwarded-Preferred-Username")
    subject = preferred or headers.get("X-Auth-Request-User") or headers.get("X-Forwarded-User")
    email = headers.get("X-Auth-Request-Email") or headers.get("X-Forwarded-Email")
    return canonical_subject(subject, email, IDENTITY_ALIASES)


def member_for(user: str) -> dict:
    return authorization(auth_state(), user)


def owned_by(record: dict, user: str) -> bool:
    try:
        return canonical_subject(record.get("subject"), None, IDENTITY_ALIASES) == user
    except ValueError:
        return False


def checked_subscription(path: str) -> tuple[str, dict, dict, str]:
    relative = path.removeprefix("/s/").split("?", 1)[0]
    suffix = ""
    for candidate in ("/mihomo.yaml", "/shadowrocket.conf"):
        if relative.endswith(candidate):
            relative, suffix = relative[:-len(candidate)], candidate
            break
    token = relative.strip("/")
    payload = verify_token(HMAC_SECRET, token)
    record = devices_state().get(payload["jti"])
    if not record or not record.get("enabled") or record.get("tokenHash") != hashlib.sha256(token.encode()).hexdigest():
        raise PermissionError("subscription revoked")
    member_for(canonical_subject(record.get("subject"), None, IDENTITY_ALIASES))
    return token, payload, record, suffix


def checked_rule(path: str) -> tuple[str, Path]:
    relative = path.removeprefix("/r/").split("?", 1)[0]
    token, marker, filename = relative.partition("/current/")
    if not marker or not filename or Path(filename).name != filename:
        raise ValueError("invalid rule path")
    payload = verify_token(HMAC_SECRET, token)
    record = devices_state().get(payload["jti"])
    if not record or not record.get("enabled") or record.get("tokenHash") != hashlib.sha256(token.encode()).hexdigest():
        raise PermissionError("subscription revoked")
    member_for(canonical_subject(record.get("subject"), None, IDENTITY_ALIASES))
    return token, RULES_ROOT / "current" / filename


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, raw: bytes, content_type: str, headers: dict[str, str] | None = None, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def json(self, status: int, value: object) -> None:
        self.send(status, (json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode(), "application/json")

    def body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length) or b"{}")

    def device_view(self, key: str, value: dict, usage: dict) -> dict:
        stats = usage.get(key, {})
        return {"id": key, "name": value.get("name", "device"), "enabled": bool(value.get("enabled")),
                "expiresAt": value.get("expiresAt"), "lastSubscriptionAt": value.get("lastSubscriptionAt"),
                "lastSeenAt": stats.get("lastSeenAt"), "activeConnections": int(stats.get("activeConnections", 0)),
                "upload": int(stats.get("upload", 0)), "download": int(stats.get("download", 0))}

    def do_GET(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self.json(200, {"ok": True})
            if path.startswith("/s/"):
                token, payload, record, suffix = checked_subscription(self.path)
                now = int(time.time())
                update_devices(lambda devices: devices[payload["jti"]].update({"lastSubscriptionAt": now}))
                password = proxy_password(HMAC_SECRET, payload["jti"])
                stats = usage_state().get(payload["jti"], {})
                headers = subscription_headers(int(stats.get("upload", 0)), int(stats.get("download", 0)))
                if suffix == "/mihomo.yaml":
                    return self.send(200, mihomo_config(password, token, BASE_URL), "text/yaml; charset=utf-8", headers)
                if suffix == "/shadowrocket.conf":
                    return self.send(200, shadowrocket_config(password, token, BASE_URL), "text/plain; charset=utf-8", headers)
                return self.send(200, subscription_body(password), "text/plain; charset=utf-8", headers)
            if path.startswith("/r/"):
                _token, file_path = checked_rule(self.path)
                raw = file_path.read_bytes()
                etag = hashlib.sha256(raw).hexdigest()
                if self.headers.get("If-None-Match", "").strip('"') == etag:
                    return self.send(304, b"", "application/octet-stream", {"ETag": f'"{etag}"'}, "private, max-age=3600")
                return self.send(200, raw, mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
                                 {"ETag": f'"{etag}"'}, "private, max-age=3600")
            user = identity(self.headers)
            member = member_for(user)
            devices, usage = devices_state(), usage_state()
            records = [self.device_view(key, value, usage) for key, value in devices.items() if owned_by(value, user)]
            if path in ("/api/me", "/api/devices"):
                return self.json(200, {"profile": "access re8ch com", "account": user, "admin": bool(member.get("admin")), "devices": records})
            if path == "/api/status":
                cache = auth_state()
                manifest = json.loads((RULES_ROOT / "current" / "manifest.json").read_text()) if (RULES_ROOT / "current" / "manifest.json").exists() else {}
                return self.json(200, {"profile": "access re8ch com", "quota": "300 GB soft", "nodes": [public_node(n) for n in DEFAULT_NODES],
                                       "rulesVersion": manifest.get("version"), "authorizationLastSuccess": cache.get("lastSuccess")})
            if path == "/api/admin/users":
                if not member.get("admin"):
                    raise PermissionError("administrator required")
                by_user = {}
                for key, value in devices.items():
                    by_user.setdefault(value.get("subject", "unknown"), []).append(self.device_view(key, value, usage))
                return self.json(200, {"members": auth_state().get("members", {}), "devices": by_user})
            if path == "/admin":
                if not member.get("admin"):
                    raise PermissionError("administrator required")
                return self.send(200, ADMIN.encode(), "text/html; charset=utf-8")
            if path == "/":
                return self.send(200, PORTAL.encode(), "text/html; charset=utf-8")
            return self.json(404, {"error": "not found"})
        except FileNotFoundError:
            self.json(503, {"error": "rule snapshot unavailable"})
        except (PermissionError, ValueError) as exc:
            self.json(401, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def do_POST(self) -> None:
        try:
            user = identity(self.headers)
            member_for(user)
            path = self.path.split("?", 1)[0]
            if path != "/api/devices" and not (path.startswith("/api/devices/") and path.endswith("/rotate")):
                return self.json(404, {"error": "not found"})
            body = self.body()
            old_id = path.removeprefix("/api/devices/").removesuffix("/rotate").strip("/") if path.endswith("/rotate") else ""
            current = devices_state().get(old_id) if old_id else None
            if old_id and (not current or not owned_by(current, user)):
                raise PermissionError("device not found")
            name = (current or {}).get("name") or str(body.get("name", "device")).strip()[:64] or "device"
            token = issue_token(HMAC_SECRET, user, name)
            payload = verify_token(HMAC_SECRET, token)
            def mutate(devices):
                if old_id:
                    if old_id not in devices or not owned_by(devices[old_id], user):
                        raise PermissionError("device not found")
                    devices[old_id]["enabled"] = False
                devices[payload["jti"]] = {"subject": user, "name": name, "jti": payload["jti"], "enabled": True,
                                             "expiresAt": payload["exp"], "tokenHash": hashlib.sha256(token.encode()).hexdigest()}
            update_devices(mutate)
            root = f"{BASE_URL}/s/{token}"
            return self.json(201, {"id": payload["jti"], "subscriptionURL": root, "mihomoURL": root + "/mihomo.yaml",
                                   "shadowrocketURL": root + "/shadowrocket.conf",
                                   "shadowrocketImportURL": shadowrocket_import_url(root + "/shadowrocket.conf")})
        except (PermissionError, ValueError) as exc:
            self.json(400, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def do_DELETE(self) -> None:
        try:
            user = identity(self.headers)
            member = member_for(user)
            admin_path = self.path.startswith("/api/admin/devices/")
            if not self.path.startswith("/api/devices/") and not admin_path:
                return self.json(404, {"error": "not found"})
            if admin_path and not member.get("admin"):
                raise PermissionError("administrator required")
            prefix = "/api/admin/devices/" if admin_path else "/api/devices/"
            device_id = self.path.removeprefix(prefix).split("/", 1)[0]
            def mutate(devices):
                record = devices.get(device_id)
                if not record or (not admin_path and not owned_by(record, user)):
                    raise PermissionError("device not found")
                record["enabled"] = False
            update_devices(mutate)
            self.send(204, b"", "application/json")
        except PermissionError as exc:
            self.json(404, {"error": str(exc)})
        except Exception as exc:
            self.json(503, {"error": type(exc).__name__})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("HTTP_PORT", "8080"))), Handler).serve_forever()
