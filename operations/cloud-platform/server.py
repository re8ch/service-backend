#!/usr/bin/env python3
"""Streamable HTTP MCP facade for the typed Cloud Platform runtime."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from runtime import CloudPlatform, Identity, Inventory, OperationError, OperationRunner

HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
PORT = int(os.getenv("LISTEN_PORT", "19443"))
SERVICE_TOKEN = os.getenv("SERVICE_API_TOKEN", "")
DEX_USERINFO = os.getenv("DEX_USERINFO_URL", "https://auth.re8ch.com/userinfo")
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "re8ch:k8s-admins")
OAUTH_AUDIENCE = os.getenv("OAUTH_AUDIENCE", "re8ch-cloud-platform-tool")
PUBLIC_MCP_URL = os.getenv("PUBLIC_MCP_URL", "https://operate.tools.re8ch.com/cloud/mcp")
RESOURCE_METADATA_URL = "https://operate.tools.re8ch.com/.well-known/oauth-protected-resource/cloud/mcp"
ROOT = Path(os.getenv("CONFIG_ROOT", "/etc/re8ch-platform"))
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/srv/service-backend"))

SCHEMAS: dict[str, dict[str, Any]] = {
    "credentials.webmail-session.ensure": {"apply": {"type": "boolean", "default": False}},
    "plugin.capabilities.list": {},
    "plugin.operations.list": {"groupVersion": {"type": "string"}},
    "plugin.operation.read": {"groupVersion": {"type": "string"}, "operationId": {"type": "string"}, "schemaHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "parameters": {"type": "object"}},
    "plugin.operation.http-check": {"groupVersion": {"type": "string"}, "operationId": {"type": "string"}, "schemaHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "parameters": {"type": "object"}},
    "plugin.openapi.get": {"groupVersion": {"type": "string", "pattern": r"^(?:api/[a-z0-9]+|apis/[a-z0-9.-]+/[a-z0-9]+)$"}},
    "nodes.list": {},
    "nodes.get": {"name": {"type": "string"}},
    "nodes.reachability": {"name": {"type": "string"}},
    "node.ssh.exec": {"name": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer", "minimum": 1, "maximum": 3600}},
    "kubectl.exec": {"argv": {"type": "array", "items": {"type": "string"}}, "stdin": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 3600}},
    **{f"kube.{resource}": {"namespace": {"type": "string"}, "name": {"type": "string"}, "labels": {"type": "string"}, "fields": {"type": "string"}} for resource in ("nodes", "namespaces", "pods", "deployments", "services", "endpoints", "secrets")},
    **{f"kube.resource.{verb}": {"resource": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}, "manifest": {"type": "object"}, "resourceVersion": {"type": "string"}} for verb in ("get", "list", "watch", "apply", "patch", "delete")},
    "kube.events.query": {"namespace": {"type": "string"}, "involvedObject": {"type": "string"}, "reason": {"type": "string"}, "type": {"type": "string"}},
    "kube.events.watch": {"namespace": {"type": "string"}, "involvedObject": {"type": "string"}, "reason": {"type": "string"}, "type": {"type": "string"}},
    "workspace.list": {},
    "workspace.status": {},
    "workspace.scan": {},
    "workspace.doctor": {},
    "workspace.index": {"write": {"type": "boolean"}},
    "workspace.sync": {"write": {"type": "boolean"}},
    "workspace.context": {"module": {"type": "string"}, "query": {"type": "string"}},
    "workspace.memory-audit": {},
}

from mihomos_capabilities import TOOLS as MIHOMOS_TOOLS
MIHOMOS_BY_NAME = {tool["name"]: tool for tool in MIHOMOS_TOOLS}
from anatomy_diff_operation import TOOL as ANATOMY_DIFF_TOOL, DELETE_TOOL as ANATOMY_DIFF_DELETE_TOOL
from cilium_cidr_operation import TOOL as CILIUM_CIDR_TOOL, DELETE_TOOL as CILIUM_CIDR_DELETE_TOOL
from network_native_mutations import BY_NAME as NETWORK_MUTATIONS
from stalwart_admin import TOOLS as STALWART_ADMIN_TOOLS, BY_NAME as STALWART_ADMIN_BY_NAME

GENERATED_PLUGIN_TOOLS = {
    item["name"]: item for item in json.loads(
        Path(__file__).with_name("plugin-operation-tools.json").read_text()
    )["tools"]
}
PRIVILEGED_TOOLS = {*STALWART_ADMIN_BY_NAME, *MIHOMOS_BY_NAME, ANATOMY_DIFF_TOOL["name"], ANATOMY_DIFF_DELETE_TOOL["name"], CILIUM_CIDR_TOOL["name"], CILIUM_CIDR_DELETE_TOOL["name"], *NETWORK_MUTATIONS, "plugin.operation.read", "plugin.operation.http-check", *GENERATED_PLUGIN_TOOLS}

WRITE_TOOLS = {"credentials.webmail-session.ensure", "node.ssh.exec", "kubectl.exec", "kube.resource.apply", "kube.resource.patch", "kube.resource.delete", "workspace.index", "workspace.sync"}


REQUIRED = {
    "plugin.operations.list": ["groupVersion"],
    "plugin.operation.read": ["groupVersion", "operationId", "schemaHash"],
    "plugin.operation.http-check": ["groupVersion", "operationId", "schemaHash"],
    "plugin.openapi.get": ["groupVersion"],
    "nodes.get": ["name"], "nodes.reachability": ["name"], "node.ssh.exec": ["name", "argv"], "kubectl.exec": ["argv"],
    **{f"kube.resource.{verb}": ["resource"] for verb in ("get", "list", "watch", "apply", "patch", "delete")},
}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def tool_list(identity: Identity) -> list[dict[str, Any]]:
    writable = ADMIN_GROUP in identity.groups
    result = []
    for name, properties in SCHEMAS.items():
        if name in WRITE_TOOLS | PRIVILEGED_TOOLS and not writable:
            continue
        description = ("Validate one bound native GET response with Schemathesis; returns checks only, not response contents. Admin only; no whole-operation acceptance claim."
                       if name == "plugin.operation.http-check" else f"RE8CH typed operation {name}")
        result.append({"name": name, "description": description, "inputSchema": schema(properties, REQUIRED.get(name))})
    if writable:
        result.extend(STALWART_ADMIN_TOOLS)
        result.extend(MIHOMOS_TOOLS)
        result.append(ANATOMY_DIFF_TOOL)
        result.append(ANATOMY_DIFF_DELETE_TOOL)
        result.extend({key: item[key] for key in ("name", "description", "inputSchema", "annotations", "_meta")}
                      for item in GENERATED_PLUGIN_TOOLS.values())
    return result


def authenticate(header: str) -> Identity | None:
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ")
    if SERVICE_TOKEN and token == SERVICE_TOKEN:
        return Identity("service:toolhive", (ADMIN_GROUP,))
    try:
        request = urllib.request.Request(
            DEX_USERINFO,
            headers={
                "Authorization": header,
                "Accept": "application/json",
                "User-Agent": "RE8CH-Cloud-Platform/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            claims = json.load(response)
        encoded = token.split(".")[1]
        token_claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        audience = token_claims.get("aud", [])
        audiences = {audience} if isinstance(audience, str) else set(audience)
        if OAUTH_AUDIENCE not in audiences:
            return None
        return Identity(str(claims["sub"]), tuple(claims.get("groups", [])))
    except Exception:
        return None


def dispatch(service: CloudPlatform, name: str, args: dict[str, Any], identity: Identity) -> Any:
    if name in WRITE_TOOLS | PRIVILEGED_TOOLS and ADMIN_GROUP not in identity.groups:
        raise OperationError("AUTHORIZATION_DENIED", name)
    if name in STALWART_ADMIN_BY_NAME:
        from stalwart_admin import call
        return call(name, args, identity.subject)
    if name == "credentials.webmail-session.ensure":
        if set(args) - {"apply"} or not isinstance(args.get("apply", False), bool):
            raise OperationError("INVALID_ARGUMENT", "only boolean apply is accepted")
        return service.ensure_webmail_session_secret(apply=args.get("apply", False))
    if name == "plugin.operation.http-check":
        from plugin_http_conformance import conformance
        return conformance(service, args.get("groupVersion"), args.get("operationId"), args.get("schemaHash"), args.get("parameters"))
    if name in MIHOMOS_BY_NAME:
        from jsonschema import Draft202012Validator
        import mihomos_capabilities
        if list(Draft202012Validator(MIHOMOS_BY_NAME[name]["inputSchema"]).iter_errors(args)):
            raise OperationError("INVALID_ARGUMENT", "Arguments do not match the Mihomos tool schema")
        if name == "mihomos.workloads.list":
            return mihomos_capabilities.workloads(service)
        if name == "mihomos.controllers.list":
            return mihomos_capabilities.controllers(service)
        return mihomos_capabilities.controller_read(service, args["pod"], args["uid"], MIHOMOS_BY_NAME[name]["_meta"]["re8ch"]["operationId"], args.get("path"))
    if name == ANATOMY_DIFF_TOOL["name"]:
        from anatomy_diff_operation import call
        return call(service, args)
    if name == ANATOMY_DIFF_DELETE_TOOL["name"]:
        from anatomy_diff_operation import delete
        return delete(service, args)
    if name == CILIUM_CIDR_TOOL["name"]:
        from cilium_cidr_operation import create
        return create(service, args)
    if name == CILIUM_CIDR_DELETE_TOOL["name"]:
        from cilium_cidr_operation import delete
        return delete(service, args)
    if name in NETWORK_MUTATIONS:
        from network_native_mutations import call
        return call(service, name, args)
    if name in GENERATED_PLUGIN_TOOLS:
        import plugin_capabilities
        item = GENERATED_PLUGIN_TOOLS[name]
        return plugin_capabilities.read_operation(service, item["groupVersion"], item["operationId"], item["schemaHash"], args)
    if name in {"plugin.capabilities.list", "plugin.openapi.get", "plugin.operations.list", "plugin.operation.read"}:
        import plugin_capabilities
        if name == "plugin.capabilities.list":
            return plugin_capabilities.catalog(service)
        if name == "plugin.operations.list":
            return plugin_capabilities.operations(service, args.get("groupVersion"))
        if name == "plugin.operation.read":
            return plugin_capabilities.read_operation(service, args.get("groupVersion"), args.get("operationId"), args.get("schemaHash"), args.get("parameters"))
        return plugin_capabilities.openapi(service, args.get("groupVersion"))
    if name == "nodes.list":
        return service.nodes_list()
    if name == "nodes.get":
        return service.nodes_get(args["name"])
    if name == "nodes.reachability":
        return service.node_reachability(args["name"])
    if name == "node.ssh.exec":
        return service.node_ssh_exec(args["name"], args["argv"], args.get("timeout", 30))
    if name == "kubectl.exec":
        return service.kubectl_exec(args["argv"], stdin=args.get("stdin"), timeout=args.get("timeout", 30))
    if name.startswith("workspace."):
        command = name.removeprefix("workspace.")
        argv = ["python", "/app/workspace.py", "--root", str(WORKSPACE_ROOT), command]
        if command in {"sync", "index"} and args.get("write"):
            argv.append("--write")
        elif command == "context":
            if args.get("module"):
                argv += ["--module", args["module"]]
            if args.get("query"):
                argv += ["--query", args["query"]]
        completed = subprocess.run(argv, text=True, capture_output=True, timeout=3600, check=False)
        if completed.returncode:
            raise OperationError("WORKSPACE_OPERATION_FAILED", completed.stderr[-2048:])
        return json.loads(completed.stdout)
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
        elif self.path == "/.well-known/oauth-protected-resource/cloud/mcp":
            self.reply(200, {"resource": PUBLIC_MCP_URL, "authorization_servers": ["https://auth.re8ch.com"], "bearer_methods_supported": ["header"], "scopes_supported": ["openid", "profile", "email", "groups", "offline_access"]})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            return self.reply(404, {"error": "not_found"})
        identity = authenticate(self.headers.get("Authorization", ""))
        if identity is None:
            challenge = f'Bearer realm="https://auth.re8ch.com", resource_metadata="{RESOURCE_METADATA_URL}", scope="openid profile email groups offline_access"'
            return self.reply(401, {"error": "unauthenticated"}, {"WWW-Authenticate": challenge})
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

    def reply(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    confidential_nodes = Path(os.getenv("NODE_INVENTORY_PATH", "/etc/re8ch-platform/infrastructure/compat-nodes.yaml"))
    inventory = Inventory([confidential_nodes, ROOT / "inventory/devices.yaml", ROOT / "inventory/cloud-providers.yaml"])
    service = CloudPlatform(inventory, OperationRunner(), kubeconfig=os.getenv("KUBECONFIG", "/etc/re8ch-platform/kubeconfig"), ssh_config=os.getenv("SSH_CONFIG", "/etc/re8ch-platform/ssh_config"))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.service = service
    server.serve_forever()


if __name__ == "__main__":
    main()
