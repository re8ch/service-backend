#!/usr/bin/env python3
"""Generic, policy-bounded RE8CH plugin-to-MCP runtime.

Plugin definitions are JSON documents (JSON is valid YAML) kept in Git.  The
runtime derives MCP tools from reviewed Kubernetes resources, OpenAPI
operations and fixed CLI commands.  It never accepts arbitrary shell text.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8088"))
DEFINITIONS_DIR = Path(os.getenv("PLUGIN_DEFINITIONS_DIR", "/etc/re8ch-plugins/definitions"))
SYNC_INTERVAL = int(os.getenv("PLUGIN_SYNC_INTERVAL_SECONDS", "30"))
KUBE_API = os.getenv("KUBERNETES_SERVICE_HOST")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
KUBE_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
KUBE_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
MAX_OUTPUT = int(os.getenv("PLUGIN_MAX_OUTPUT_BYTES", str(2 * 1024 * 1024)))
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")
SAFE_FIELD_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
SECRET_WORDS = {"secret", "password", "token", "privatekey", "clientsecret", "apikey"}


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = required
    return result


def unavailable(plugin: str, reason: str, last_healthy: str | None = None) -> dict[str, Any]:
    return {"code": "PLUGIN_UNAVAILABLE", "plugin": plugin, "reason": reason,
            "lastHealthyAt": last_healthy, "retryable": True}


class PluginError(Exception):
    pass


class MethodNotFound(PluginError):
    pass


class PluginStore:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.plugins: dict[str, dict[str, Any]] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self.previous: tuple[dict[str, Any], dict[str, Any]] | None = None
        self.generation = 0
        self.revision = ""
        self.last_sync: str | None = None
        self.drift: dict[str, bool] = {}
        self.sync()

    def _read(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text())
            if raw.get("apiVersion") != "plugins.re8ch.com/v1alpha1" or raw.get("kind") != "PluginDefinition":
                raise PluginError(f"{path.name}: unsupported PluginDefinition version")
            name = raw.get("metadata", {}).get("name", "")
            if not NAME_RE.fullmatch(name):
                raise PluginError(f"{path.name}: invalid plugin name")
            if name in found:
                raise PluginError(f"duplicate plugin {name}")
            self._validate(raw)
            found[name] = raw
        return found

    def _validate(self, plugin: dict[str, Any]) -> None:
        spec = plugin.get("spec", {})
        source_type = spec.get("source", {}).get("type")
        if source_type not in {"kubernetes", "openapi", "cli", "remoteMcp"}:
            raise PluginError("source.type must be kubernetes, openapi, cli, or remoteMcp")
        policy = spec.get("policy", {})
        if not isinstance(policy.get("allowedOperations", []), list):
            raise PluginError("policy.allowedOperations must be a list")
        denied = {str(x).lower().replace("_", "") for x in policy.get("deniedFields", [])}
        if denied.intersection(SECRET_WORDS) != denied:
            raise PluginError("deniedFields may contain only recognized sensitive field names")
        if source_type == "cli":
            for operation in spec.get("discovery", {}).get("commands", []):
                argv = operation.get("argv")
                if not argv or not isinstance(argv, list) or any(not isinstance(x, str) for x in argv):
                    raise PluginError("CLI commands require a fixed argv array")
                if any(x in {"sh", "bash", "-c", "--command"} for x in argv):
                    raise PluginError("shell interpreters and free-form command flags are forbidden")

    def sync(self) -> dict[str, Any]:
        candidate = self._read()
        tools = derive_all(candidate)
        revision = fingerprint(candidate)
        with self.lock:
            changed = revision != self.revision
            if changed and self.plugins:
                self.previous = (copy.deepcopy(self.plugins), copy.deepcopy(self.tools))
            self.plugins, self.tools, self.revision = candidate, tools, revision
            if changed:
                self.generation += 1
            self.last_sync = timestamp()
        return self.summary(changed=changed)

    def summary(self, changed: bool | None = None) -> dict[str, Any]:
        result = {"generation": self.generation, "revision": self.revision,
                  "pluginCount": len(self.plugins), "toolCount": len(self.tools), "lastSyncAt": self.last_sync}
        if changed is not None:
            result["changed"] = changed
        return result

    def effective_enabled(self, name: str) -> bool:
        if name in self.drift:
            return self.drift[name]
        return bool(self.plugins[name].get("spec", {}).get("publication", {}).get("enabled", False))

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        with self.lock:
            if name not in self.plugins:
                raise PluginError("unknown plugin")
            self.drift[name] = enabled
            return {"plugin": name, "enabled": enabled, "drift": True,
                    "desiredState": "Git PluginDefinition must be updated"}

    def rollback(self) -> dict[str, Any]:
        with self.lock:
            if not self.previous:
                raise PluginError("no previous generation")
            self.plugins, self.tools = self.previous
            self.previous = None
            self.generation += 1
            self.revision = fingerprint(self.plugins)
            return self.summary(changed=True)


def tool_name(plugin: dict[str, Any], resource: str, operation: str) -> str:
    prefix = plugin["spec"].get("publication", {}).get("toolPrefix") or plugin["metadata"]["name"]
    return ".".join((prefix, resource, operation)).lower().replace("_", "-")


def common_scope_schema(scope: dict[str, Any]) -> dict[str, Any]:
    namespaced = bool(scope.get("namespaced", True))
    props: dict[str, Any] = {"name": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+$"}}
    required: list[str] = []
    if namespaced:
        allowed = scope.get("namespaces", [])
        props["namespace"] = {"type": "string", "enum": allowed} if allowed else {"type": "string"}
        required.append("namespace")
    return object_schema(props, required)


def derive_kubernetes(plugin: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    allowed = set(plugin["spec"].get("policy", {}).get("allowedOperations", []))
    for scope in plugin["spec"].get("policy", {}).get("resourceScopes", []):
        resource = scope["resource"]
        base = common_scope_schema(scope)
        for operation in sorted(allowed.intersection(scope.get("verbs", []))):
            schema = copy.deepcopy(base)
            if operation in {"create", "patch", "apply"}:
                schema["properties"]["object"] = {"type": "object"}
                schema.setdefault("required", []).append("object")
                if operation != "create":
                    schema.setdefault("required", []).append("name")
            elif operation in {"get", "delete"}:
                schema.setdefault("required", []).append("name")
            result.append({"name": tool_name(plugin, resource, operation),
                           "description": f"{operation} reviewed {resource} resources through native Kubernetes RBAC.",
                           "inputSchema": schema, "plugin": plugin["metadata"]["name"],
                           "adapter": "kubernetes", "operation": operation, "scope": scope,
                           "write": operation not in {"get", "list", "watch"}})
    return result


def derive_cli(plugin: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    allowed = set(plugin["spec"].get("policy", {}).get("allowedOperations", []))
    for command in plugin["spec"].get("discovery", {}).get("commands", []):
        if command["name"] not in allowed:
            continue
        result.append({"name": tool_name(plugin, command.get("resource", "cli"), command["name"]),
                       "description": command.get("description", "Run a fixed reviewed CLI command."),
                       "inputSchema": object_schema(command.get("parameters", {})),
                       "plugin": plugin["metadata"]["name"], "adapter": "cli",
                       "operation": command["name"], "command": command,
                       "write": bool(command.get("write", False))})
    return result


def derive_openapi(plugin: dict[str, Any]) -> list[dict[str, Any]]:
    # Definitions may pin reviewed operations. Live OpenAPI refresh can enrich
    # these schemas without ever widening the allowlist.
    result = []
    allowed = set(plugin["spec"].get("policy", {}).get("allowedOperations", []))
    for operation in plugin["spec"].get("discovery", {}).get("operations", []):
        if operation["operationId"] not in allowed:
            continue
        method = operation.get("method", "GET").upper()
        result.append({"name": tool_name(plugin, operation.get("resource", "api"), operation["operationId"]),
                       "description": operation.get("description", f"Call reviewed OpenAPI operation {operation['operationId']}"),
                       "inputSchema": object_schema(operation.get("parameters", {})),
                       "plugin": plugin["metadata"]["name"], "adapter": "openapi",
                       "operation": operation["operationId"], "api": operation,
                       "write": method not in {"GET", "HEAD", "OPTIONS"}})
    return result


def derive_all(plugins: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tools: dict[str, dict[str, Any]] = {}
    for plugin in plugins.values():
        kind = plugin["spec"]["source"]["type"]
        derived = derive_kubernetes(plugin) if kind == "kubernetes" else derive_cli(plugin) if kind == "cli" else derive_openapi(plugin) if kind == "openapi" else []
        for item in derived:
            if item["name"] in tools:
                raise PluginError(f"tool name conflict: {item['name']}")
            item["schemaHash"] = fingerprint(item["inputSchema"])
            tools[item["name"]] = item
    return tools


class Executor:
    def __init__(self, store: PluginStore):
        self.store = store
        self.health: dict[str, dict[str, Any]] = {}

    def _kube(self, method: str, path: str, body: Any | None = None, content_type: str = "application/json",
              identity_ref: str | None = None) -> Any:
        if not KUBE_API or not KUBE_TOKEN.exists():
            raise PluginError("kubernetes_service_account_unavailable")
        headers = {"Authorization": "Bearer " + KUBE_TOKEN.read_text().strip(), "Content-Type": content_type}
        if identity_ref:
            headers["Impersonate-User"] = f"system:serviceaccount:toolhive-system:{identity_ref}"
        request = urllib.request.Request(f"https://{KUBE_API}:{KUBE_PORT}{path}", method=method,
            data=canonical(body) if body is not None else None, headers=headers)
        context = ssl.create_default_context(cafile=KUBE_CA)
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            raw = response.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise PluginError("response_too_large")
            return json.loads(raw or b"{}")

    @staticmethod
    def _resource_path(scope: dict[str, Any], args: dict[str, Any]) -> str:
        group, version, resource = scope.get("group", ""), scope["version"], scope["resource"]
        root = f"/apis/{group}/{version}" if group else f"/api/{version}"
        if scope.get("namespaced", True):
            namespace = args.get("namespace", "")
            if scope.get("namespaces") and namespace not in scope["namespaces"]:
                raise PluginError("namespace_not_allowed")
            root += f"/namespaces/{urllib.parse.quote(namespace, safe='')}"
        path = root + "/" + urllib.parse.quote(resource, safe="")
        if args.get("name"):
            path += "/" + urllib.parse.quote(args["name"], safe="")
        return path

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        item = self.store.tools.get(name)
        if not item:
            raise PluginError("unknown tool")
        plugin_name = item["plugin"]
        if not self.store.effective_enabled(plugin_name):
            return unavailable(plugin_name, "disabled", self.health.get(plugin_name, {}).get("lastHealthyAt"))
        try:
            if item["adapter"] == "kubernetes":
                result = self._execute_kubernetes(item, args)
            elif item["adapter"] == "cli":
                result = self._execute_cli(item, args)
            elif item["adapter"] == "openapi":
                result = self._execute_openapi(item, args)
            else:
                raise PluginError("unsupported_adapter")
            self.health[plugin_name] = {"available": True, "lastHealthyAt": timestamp()}
            return {"operationId": "op_" + uuid.uuid4().hex, "plugin": plugin_name,
                    "generation": self.store.generation, "result": result}
        except (PluginError, urllib.error.URLError, subprocess.SubprocessError) as exc:
            self.health[plugin_name] = {"available": False, "reason": str(exc),
                                        "lastHealthyAt": self.health.get(plugin_name, {}).get("lastHealthyAt")}
            return unavailable(plugin_name, str(exc), self.health[plugin_name].get("lastHealthyAt"))

    def _execute_kubernetes(self, item: dict[str, Any], args: dict[str, Any]) -> Any:
        operation = item["operation"]
        path = self._resource_path(item["scope"], args)
        identity_ref = self.store.plugins[item["plugin"]]["spec"].get("identityRef")
        if operation in {"get", "list"}:
            return self._kube("GET", path, identity_ref=identity_ref)
        if operation == "delete":
            return self._kube("DELETE", path, {"kind": "DeleteOptions", "apiVersion": "v1",
                                                "preconditions": {"resourceVersion": args.get("resourceVersion")}},
                              identity_ref=identity_ref)
        body = copy.deepcopy(args["object"])
        denied = {x.lower().replace("_", "") for x in self.store.plugins[item["plugin"]]["spec"].get("policy", {}).get("deniedFields", [])}
        if contains_denied(body, denied):
            raise PluginError("denied_sensitive_field")
        if operation == "create":
            return self._kube("POST", path, body, identity_ref=identity_ref)
        if operation == "patch":
            return self._kube("PATCH", path, body, "application/merge-patch+json", identity_ref)
        if operation == "apply":
            query = "?fieldManager=re8ch-plugin-runtime&force=false"
            return self._kube("PATCH", path + query, body, "application/apply-patch+yaml", identity_ref)
        raise PluginError("operation_not_implemented")

    def _execute_cli(self, item: dict[str, Any], args: dict[str, Any]) -> Any:
        command = item["command"]
        argv = list(command["argv"])
        for key, spec in command.get("parameters", {}).items():
            if key not in args:
                continue
            value = args[key]
            if not isinstance(value, (str, int, bool)) or not SAFE_FIELD_RE.fullmatch(str(value)):
                raise PluginError("unsafe_cli_parameter")
            argv.extend([spec.get("flag", "--" + key.replace("_", "-")), str(value).lower() if isinstance(value, bool) else str(value)])
        proc = subprocess.run(argv, cwd=command.get("workingDirectory", "/tmp"), capture_output=True,
                              timeout=min(int(command.get("timeoutSeconds", 30)), 300), check=False, text=True)
        return {"argv": argv, "exitCode": proc.returncode, "stdout": proc.stdout[:MAX_OUTPUT],
                "stderr": proc.stderr[:MAX_OUTPUT], "truncated": len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT}

    def _execute_openapi(self, item: dict[str, Any], args: dict[str, Any]) -> Any:
        api = item["api"]
        plugin = self.store.plugins[item["plugin"]]
        base = plugin["spec"]["source"]["baseUrl"].rstrip("/")
        path = api["path"]
        query = {}
        for key, value in args.items():
            marker = "{" + key + "}"
            if marker in path:
                path = path.replace(marker, urllib.parse.quote(str(value), safe=""))
            else:
                query[key] = value
        url = base + path
        method = api.get("method", "GET").upper()
        data = None
        if method in {"GET", "HEAD"}:
            if query:
                url += "?" + urllib.parse.urlencode(query)
        else:
            data = canonical(query)
        request = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise PluginError("response_too_large")
            return json.loads(raw or b"{}")


def contains_denied(value: Any, denied: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("_", "")
            if normalized in denied or contains_denied(child, denied):
                return True
    elif isinstance(value, list):
        return any(contains_denied(child, denied) for child in value)
    return False


STORE = PluginStore(DEFINITIONS_DIR)
EXECUTOR = Executor(STORE)


def management_tools() -> list[dict[str, Any]]:
    empty = object_schema({})
    plugin = object_schema({"plugin": {"type": "string"}}, ["plugin"])
    return [
        {"name": "plugins.list", "description": "List declared plugins and runtime state.", "inputSchema": empty},
        {"name": "plugins.get", "description": "Get one sanitized PluginDefinition and status.", "inputSchema": plugin},
        {"name": "plugins.health", "description": "Get plugin availability and last healthy timestamps.", "inputSchema": empty},
        {"name": "plugins.diff", "description": "List emergency runtime overrides that differ from Git.", "inputSchema": empty},
        {"name": "plugins.sync", "description": "Reload and atomically publish valid Git definitions.", "inputSchema": empty},
        {"name": "plugins.enable", "description": "Emergency-enable a plugin and record Git drift.", "inputSchema": plugin},
        {"name": "plugins.disable", "description": "Emergency-disable a plugin and record Git drift.", "inputSchema": plugin},
        {"name": "plugins.rollback", "description": "Rollback to the previous validated generation.", "inputSchema": empty},
    ]


def listed_tools() -> list[dict[str, Any]]:
    result = management_tools()
    with STORE.lock:
        for item in STORE.tools.values():
            public = {key: item[key] for key in ("name", "description", "inputSchema")}
            public["annotations"] = {"readOnlyHint": not item["write"], "destructiveHint": item["operation"] == "delete"}
            result.append(public)
    return result


def call(name: str, args: dict[str, Any]) -> Any:
    if name == "plugins.list":
        return [{"name": key, "enabled": STORE.effective_enabled(key), "sourceType": value["spec"]["source"]["type"],
                 "toolCount": sum(1 for tool in STORE.tools.values() if tool["plugin"] == key)} for key, value in STORE.plugins.items()]
    if name == "plugins.get":
        value = STORE.plugins.get(args["plugin"])
        if not value:
            raise PluginError("unknown plugin")
        return {"definition": value, "enabled": STORE.effective_enabled(args["plugin"]),
                "health": EXECUTOR.health.get(args["plugin"], {"available": None})}
    if name == "plugins.health":
        return {key: EXECUTOR.health.get(key, {"available": None}) for key in STORE.plugins}
    if name == "plugins.diff":
        return {"runtimeOverrides": STORE.drift, "requiresGitReconciliation": bool(STORE.drift)}
    if name == "plugins.sync":
        return STORE.sync()
    if name in {"plugins.enable", "plugins.disable"}:
        return STORE.set_enabled(args["plugin"], name.endswith("enable"))
    if name == "plugins.rollback":
        return STORE.rollback()
    return EXECUTOR.execute(name, args)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, value: Any) -> None:
        raw = canonical(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path in {"/health", "/health/liveness"}:
            self._json(200, {"status": "ok", **STORE.summary()})
        elif self.path == "/health/readiness":
            self._json(200 if STORE.plugins else 503, {"status": "ready" if STORE.plugins else "empty", **STORE.summary()})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/mcp":
            self._json(404, {"error": "not_found"}); return
        request: dict[str, Any] = {}
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-03-26"),
                          "capabilities": {"tools": {"listChanged": True}},
                          "serverInfo": {"name": "re8ch-plugin-runtime", "version": "0.1.2"}}
            elif method == "tools/list":
                result = {"tools": listed_tools()}
            elif method == "tools/call":
                params = request.get("params", {})
                value = call(params.get("name", ""), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                          "isError": isinstance(value, dict) and value.get("code") == "PLUGIN_UNAVAILABLE"}
            elif method == "notifications/initialized":
                self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
            else:
                raise MethodNotFound("method not found")
            self._json(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except MethodNotFound as exc:
            self._json(200, {"jsonrpc": "2.0", "id": request.get("id"),
                             "error": {"code": -32601, "message": str(exc)}})
        except (PluginError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(200, {"jsonrpc": "2.0", "id": request.get("id"),
                             "error": {"code": -32602, "message": str(exc)}})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(json.dumps({"timestamp": timestamp(), "component": "re8ch-plugin-runtime",
                          "message": fmt % args}, separators=(",", ":")), flush=True)


def sync_loop() -> None:
    while True:
        time.sleep(max(SYNC_INTERVAL, 5))
        try:
            STORE.sync()
        except Exception as exc:
            print(json.dumps({"timestamp": timestamp(), "event": "plugin_sync_failed", "error": str(exc)}), flush=True)


if __name__ == "__main__":
    threading.Thread(target=sync_loop, daemon=True).start()
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
