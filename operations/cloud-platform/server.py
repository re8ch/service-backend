#!/usr/bin/env python3
"""Streamable HTTP MCP facade for the typed Cloud Platform runtime."""

from __future__ import annotations

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from runtime import CloudPlatform, Identity, Inventory, OperationError, OperationRunner

HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
PORT = int(os.getenv("LISTEN_PORT", "19443"))
SERVICE_TOKEN = os.getenv("SERVICE_API_TOKEN", "")
DEX_USERINFO = os.getenv("DEX_USERINFO_URL", "https://dex.re8ch.com/userinfo")
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "re8ch:k8s-admins")
ROOT = Path(os.getenv("CONFIG_ROOT", "/etc/re8ch-platform"))

SCHEMAS: dict[str, dict[str, Any]] = {
    "nodes.list": {},
    "nodes.get": {"name": {"type": "string"}},
    "nodes.reachability": {"name": {"type": "string"}},
    "node.ssh.exec": {"name": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer", "minimum": 1, "maximum": 3600}},
    **{f"kube.{resource}": {"namespace": {"type": "string"}, "name": {"type": "string"}, "labels": {"type": "string"}, "fields": {"type": "string"}} for resource in ("nodes", "namespaces", "pods", "deployments", "services", "endpoints", "secrets")},
    **{f"kube.resource.{verb}": {"resource": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}, "manifest": {"type": "object"}, "resourceVersion": {"type": "string"}} for verb in ("get", "list", "watch", "apply", "patch", "delete")},
    "kube.events.query": {"namespace": {"type": "string"}, "involvedObject": {"type": "string"}, "reason": {"type": "string"}, "type": {"type": "string"}},
    "kube.events.watch": {"namespace": {"type": "string"}, "involvedObject": {"type": "string"}, "reason": {"type": "string"}, "type": {"type": "string"}},
}

WRITE_TOOLS = {"node.ssh.exec", "kube.resource.apply", "kube.resource.patch", "kube.resource.delete"}


REQUIRED = {
    "nodes.get": ["name"], "nodes.reachability": ["name"], "node.ssh.exec": ["name", "argv"],
    **{f"kube.resource.{verb}": ["resource"] for verb in ("get", "list", "watch", "apply", "patch", "delete")},
}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def tool_list(identity: Identity) -> list[dict[str, Any]]:
    writable = ADMIN_GROUP in identity.groups
    result = []
    for name, properties in SCHEMAS.items():
        if name in WRITE_TOOLS and not writable:
            continue
        result.append({"name": name, "description": f"RE8CH typed operation {name}", "inputSchema": schema(properties, REQUIRED.get(name))})
    return result


def authenticate(header: str) -> Identity | None:
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ")
    if SERVICE_TOKEN and token == SERVICE_TOKEN:
        return Identity("service:toolhive", (ADMIN_GROUP,))
    try:
        request = urllib.request.Request(DEX_USERINFO, headers={"Authorization": header, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            claims = json.load(response)
        return Identity(str(claims["sub"]), tuple(claims.get("groups", [])))
    except Exception:
        return None


def dispatch(service: CloudPlatform, name: str, args: dict[str, Any], identity: Identity) -> Any:
    if name in WRITE_TOOLS and ADMIN_GROUP not in identity.groups:
        raise OperationError("AUTHORIZATION_DENIED", name)
    if name == "nodes.list":
        return service.nodes_list()
    if name == "nodes.get":
        return service.nodes_get(args["name"])
    if name == "nodes.reachability":
        return service.node_reachability(args["name"])
    if name == "node.ssh.exec":
        return service.node_ssh_exec(args["name"], args["argv"], args.get("timeout", 30))
    if name.startswith("kube.resource."):
        return service.kube_resource(name.rsplit(".", 1)[1], args["resource"], namespace=args.get("namespace"), name=args.get("name"), manifest=args.get("manifest"), resource_version=args.get("resourceVersion"))
    if name.startswith("kube.events."):
        return service.kube_events(namespace=args.get("namespace"), involved_object=args.get("involvedObject"), reason=args.get("reason"), event_type=args.get("type"), watch=name.endswith(".watch"))
    if name.startswith("kube."):
        return service.kube_simple(name.removeprefix("kube."), namespace=args.get("namespace"), name=args.get("name"), labels=args.get("labels"), fields=args.get("fields"))
    raise OperationError("TOOL_NOT_FOUND", name)


class Handler(BaseHTTPRequestHandler):
    server_version = "re8ch-cloud-platform/0.1"

    def do_GET(self) -> None:
        if self.path in {"/healthz", "/readyz"}:
            self.reply(200, {"status": "ok"})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            return self.reply(404, {"error": "not_found"})
        identity = authenticate(self.headers.get("Authorization", ""))
        if identity is None:
            return self.reply(401, {"error": "unauthenticated"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(size))
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": True}}, "serverInfo": {"name": "re8ch-cloud-platform", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": tool_list(identity)}
            elif method == "tools/call":
                value = dispatch(self.server.service, request["params"]["name"], request["params"].get("arguments", {}), identity)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
            elif method == "notifications/initialized":
                return self.reply(202, {})
            else:
                raise OperationError("METHOD_NOT_FOUND", str(method))
            self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except OperationError as exc:
            self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc), "data": {"code": exc.code, "retryable": exc.retryable}}})
        except Exception as exc:
            self.reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": type(exc).__name__}})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log request bodies. Reverse proxies provide request metadata.
        print(json.dumps({"event": "http", "remote": self.client_address[0], "message": fmt % args}))

    def reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    inventory = Inventory([ROOT / "inventory/nodes.yaml", ROOT / "inventory/devices.yaml", ROOT / "inventory/cloud-providers.yaml"])
    service = CloudPlatform(inventory, OperationRunner(), kubeconfig=os.getenv("KUBECONFIG", "/etc/re8ch-platform/kubeconfig"), ssh_config=os.getenv("SSH_CONFIG", "/etc/re8ch-platform/ssh_config"))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.service = service
    server.serve_forever()


if __name__ == "__main__":
    main()
