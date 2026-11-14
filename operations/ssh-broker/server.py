"""Internal MCP server for the independently bound RE8CH SSH broker."""
from __future__ import annotations
import hmac, json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from ssh_broker_runtime import BrokerError, SSHBroker, TargetRegistry

HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
PORT = int(os.getenv("LISTEN_PORT", "19444"))
SERVICE_TOKEN = os.getenv("SERVICE_API_TOKEN", "")
TARGETS = Path(os.getenv("SSH_TARGET_REGISTRY", "/etc/re8ch-platform/infrastructure/compat-nodes.yaml"))
SSH_CONFIG = os.getenv("SSH_CONFIG", "/etc/re8ch-ai-operations/ssh-config")
SSH_USER = os.getenv("SSH_USER", "re8ch-ai")
SSH_ROUTE_OVERRIDES = json.loads(os.getenv("SSH_ROUTE_OVERRIDES", "{}"))
TOOLS = [
    {"name": "ssh.targets.list", "description": "List reviewed SSH broker targets without consulting Kubernetes.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "ssh.reachability", "description": "Check one reviewed SSH target through the managed broker identity.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "actor": {"type": "string"}}, "required": ["name", "actor"], "additionalProperties": False}},
    {"name": "ssh.exec", "description": "Execute one exact argv command through the audited SSH broker.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer", "minimum": 1, "maximum": 3600}, "actor": {"type": "string"}}, "required": ["name", "argv", "actor"], "additionalProperties": False}},
]

def authorized(header: str) -> bool:
    return bool(SERVICE_TOKEN) and header.startswith("Bearer ") and hmac.compare_digest(header.removeprefix("Bearer "), SERVICE_TOKEN)

class Handler(BaseHTTPRequestHandler):
    server_version = "re8ch-ssh-broker/1.0"
    def do_GET(self):
        self.reply(200, {"status": "ok"}) if self.path in {"/healthz", "/readyz"} else self.reply(404, {"error": "not_found"})
    def do_POST(self):
        if self.path != "/mcp": return self.reply(404, {"error": "not_found"})
        if not authorized(self.headers.get("Authorization", "")): return self.reply(401, {"error": "unauthenticated"})
        request_id = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1_048_576: raise BrokerError("INVALID_ARGUMENT", "request body is too large")
            request = json.loads(self.rfile.read(size)); request_id = request.get("id"); method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": True}}, "serverInfo": {"name": "re8ch-ssh-broker", "version": "1.0.0"}}
            elif method == "notifications/initialized": return self.reply(202, {})
            elif method == "tools/list": result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}; args = params.get("arguments") or {}; tool = params.get("name")
                if tool == "ssh.targets.list": value = {"targets": self.server.broker.registry.names()}
                elif tool == "ssh.reachability": value = self.server.broker.execute(str(args.get("name", "")), ["hostname"], 15, actor=str(args.get("actor", "")))
                elif tool == "ssh.exec":
                    argv = args.get("argv")
                    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv): raise BrokerError("INVALID_ARGUMENT", "argv must be a string array")
                    value = self.server.broker.execute(str(args.get("name", "")), argv, int(args.get("timeout", 30)), actor=str(args.get("actor", "")))
                else: raise BrokerError("TOOL_NOT_FOUND", str(tool))
                if tool in {"ssh.reachability", "ssh.exec"}:
                    print(json.dumps({"event": "ssh_audit", "operationId": value["operationId"], "actor": value["actor"], "target": value["target"], "route": value["route"], "commandHash": value["commandHash"], "exitCode": value["exitCode"], "durationMs": value["durationMs"]}))
                result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
            else: raise BrokerError("METHOD_NOT_FOUND", str(method))
            self.reply(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except BrokerError as exc:
            self.reply(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc), "data": {"code": exc.code}}})
        except (ValueError, TypeError, json.JSONDecodeError):
            self.reply(400, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "invalid request"}})
    def log_message(self, fmt, *args):
        print(json.dumps({"event": "http", "remote": self.client_address[0], "message": fmt % args}))
    def reply(self, status, body):
        payload = json.dumps(body).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler); server.broker = SSHBroker(TargetRegistry(TARGETS, route_overrides=SSH_ROUTE_OVERRIDES), ssh_config=SSH_CONFIG, user=SSH_USER); server.serve_forever()
if __name__ == "__main__": main()
