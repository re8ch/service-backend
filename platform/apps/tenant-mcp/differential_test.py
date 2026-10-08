"""Run the Python and Go tenant MCP runtimes against identical local fixtures."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[3]
PYTHON_RUNTIME = ROOT / "clusters/re8ch/components/ai-operations/ai_operations_mcp.py"
sys.path.insert(0, str(PYTHON_RUNTIME.parent))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def jwt(subject="user-1"):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    return part({"alg": "none"}) + "." + part({"iss": "https://db-rw.re8ch.com/auth/v1", "aud": "authenticated", "sub": subject, "exp": int(time.time()) + 3600}) + ".signature"


class FixtureAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/user":
            self.reply(200, {"id": "user-1", "is_anonymous": False})
        elif self.path == "/v1/services":
            self.reply(200, {"items": []})
        elif self.path == "/v1/claims":
            self.reply(200, {"items": []})
        elif self.path == "/v1/capabilities":
            self.reply(200, {"tenantRef": "acme", "grants": [
                {"name": "acme-database", "consumableRef": "database.postgresql.shared.v1", "roleRef": "operator", "lifecycle": "Active"},
                {"name": "acme-harbor", "consumableRef": "registry.harbor.v1", "roleRef": "operator", "lifecycle": "Active"},
            ]})
        elif self.path in ("/readyz", "/healthz", "/.well-known/openid-configuration"):
            self.reply(200, {"status": "ready"})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/v1/resolve-supabase":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if body.get("tenantRef") == "acme" and body.get("accessToken") == TOKEN:
                self.reply(200, {"tenantRef": "acme", "role": "operator", "defaultNamespace": "acme"})
            else:
                self.reply(403, {"error": "forbidden"})
        else:
            self.reply(404, {"error": "not_found"})

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


def request(url, method="GET", body=None, headers=None):
    payload = json.dumps(body).encode() if body is not None else None
    defaults = {"Authorization": "Bearer " + TOKEN, "X-RE8CH-Tenant": "acme", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    defaults.update(headers or {})
    req = Request(url, data=payload, method=method, headers=defaults)
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def token_exchange(base):
    data = urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "subject_token": TOKEN, "audience": "re8ch-tenant"}).encode()
    req = Request(base + "/oauth/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def mcp(base, name, params):
    status, response = request(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": name, "params": params})
    if status != 200 or "error" in response:
        raise AssertionError(f"{base} {name} failed: {status} {response}")
    return response["result"]


def content(result):
    item = result["content"][0]
    return json.loads(item["text"]) if item["type"] == "text" else item


def equivalent(label, python_value, go_value):
    if python_value != go_value:
        raise AssertionError(f"{label}\nPython: {json.dumps(python_value, ensure_ascii=False, sort_keys=True)}\nGo: {json.dumps(go_value, ensure_ascii=False, sort_keys=True)}")
    print("PASS", label)


def seed(path, module):
    module.ServiceStore(path)
    event = {"eventId": "ue_" + "a" * 32, "tenantRef": "acme", "resourceRef": "service://acme/worker", "serviceClass": "compute.workload.v1", "meter": "cpu", "quantity": 2.5, "unit": "core-hour", "periodStart": "2026-09-30T11:00:00Z", "periodEnd": "2026-09-30T12:00:00Z", "regionRef": "loudi", "sourceOperationId": "fixture", "observedAt": "2026-09-30T12:00:00Z", "schemaVersion": "usage.re8ch.com/v1", "pricingApplied": False, "accountingPosted": False}
    with sqlite3.connect(path) as db:
        spec = {"serviceClass": "compute.workload.v1", "regionRef": "loudi", "planRef": "basic", "desiredState": "Ready"}
        db.execute("INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?,?)", ("service://acme/worker", "acme", "worker", "compute.workload.v1", "Ready", json.dumps(spec), 1, "2026-09-30T10:00:00Z", "2026-09-30T10:00:00Z", None))
        db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,?)", ("binding://acme/worker/1", "acme", "service://acme/worker", "endpoint", json.dumps({"host": "worker.internal"}), None, "2026-09-30T10:00:00Z"))
        db.execute("INSERT INTO received_usage VALUES(?,?,?,?,?)", (event["eventId"], "acme", event["resourceRef"], json.dumps(event), "2026-09-30T12:00:00Z"))
        db.execute("INSERT INTO cluster_registrations VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("cluster-registration://acme/test", "acme", "cluster-test", "loudi", "https://issuer.test", "sha256:" + "a" * 64, json.dumps(["observe"]), "PendingApproval", "enrollment://acme/test", "2026-09-30T09:00:00Z", "2026-09-30T09:00:00Z"))
        db.commit()


def main():
    global TOKEN
    TOKEN = jwt()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureAPI)
        fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
        fixture_thread.start()
        fixture_url = f"http://127.0.0.1:{fixture.server_port}"
        python_db, go_db = root / "python.db", root / "go.db"
        common_env = {**os.environ, "RE8CH_TOOL_PROFILE": "tenant", "RE8CH_TOOL_AUDIENCE": "re8ch-tenant", "TENANT_CLAIM_SERVICE_TOKEN": "fixture-secret", "SUPABASE_USER_URL": fixture_url + "/user", "TENANT_CONTROL_URL": fixture_url, "AUTH_DISCOVERY_URL": fixture_url + "/.well-known/openid-configuration", "HARBOR_SUPPLY_CHAIN_URL": fixture_url, "SERVICE_API_TOKEN": "fixture-service-token"}
        os.environ.update(common_env)
        os.environ["TENANT_SERVICE_DB"] = str(python_db)
        os.environ["DEVICE_REGISTRY_PATH"] = str(root / "python-devices.db")
        spec = importlib.util.spec_from_file_location("legacy_tenant_runtime", PYTHON_RUNTIME)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        seed(python_db, module)
        with sqlite3.connect(python_db) as source, sqlite3.connect(go_db) as destination:
            source.backup(destination)
        python_server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        python_thread = threading.Thread(target=python_server.serve_forever, daemon=True)
        python_thread.start()
        python_url = f"http://127.0.0.1:{python_server.server_port}"
        binary = root / "tenant-mcp"
        subprocess.run(["go", "build", "-o", str(binary), "."], check=True, cwd=Path(__file__).parent)
        go_port, admin_port = free_port(), free_port()
        go_env = {**common_env, "TENANT_SERVICE_DB": str(go_db), "DEVICE_REGISTRY_PATH": str(root / "go-devices.db"), "DYNAMIC_SKILL_DB": str(root / "skills.db"), "DYNAMIC_SKILL_ADMIN_TOKEN": "fixture-admin-token", "LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": str(go_port), "ADMIN_LISTEN_PORT": str(admin_port)}
        process = subprocess.Popen([str(binary)], env=go_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        go_url = f"http://127.0.0.1:{go_port}"
        try:
            for _ in range(100):
                try:
                    request(go_url + "/livez")
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Go tenant server failed to start: " + process.stderr.read().decode())
            tool_cases = [
                ("service_catalog_list", {}), ("service_instance_list", {}),
                ("service_instance_get", {"resource_ref": "service://acme/worker"}),
                ("service_binding_list", {}), ("usage_summary_get", {}),
                ("usage_events_list", {}), ("byoc_registration_list", {}),
                ("byoc_connection_list", {}),
            ]
            # The Go publication intentionally scopes Artchais cluster tools to that tenant.
            # The legacy Python server still lists them for the fixture's acme tenant.
            p_tools = {item["name"]: {key: item[key] for key in ("name", "description", "inputSchema")} for item in mcp(python_url, "tools/list", {})["tools"] if not item["name"].startswith("artc_")}
            g_tools = {item["name"]: {key: item[key] for key in ("name", "description", "inputSchema")} for item in mcp(go_url, "tools/list", {})["tools"] if item["name"] in p_tools}
            equivalent("tools/list legacy schemas", p_tools, g_tools)
            assert not any(item["name"].startswith("artc_") for item in mcp(go_url, "tools/list", {})["tools"]), "Artchais tools leaked into acme catalog"
            for name, args in tool_cases:
                p = content(mcp(python_url, "tools/call", {"name": name, "arguments": args}))
                g = content(mcp(go_url, "tools/call", {"name": name, "arguments": args}))
                equivalent(name, p, g)
                if name == "service_catalog_list":
                    equivalent("service catalog only granted provider-backed classes", {item["serviceClass"] for item in p["services"]}, {"registry.harbor.v1"})
                if name == "service_instance_get":
                    equivalent("legacy intent is not reported as provisioned", (p["fulfillmentState"], p["provisioned"]), ("legacy-intent-only", False))
                if name == "service_instance_list":
                    equivalent("legacy list item is not reported as provisioned", (p["instances"][0]["fulfillmentState"], p["instances"][0]["provisioned"]), ("legacy-intent-only", False))
            p_grants = content(mcp(python_url, "tools/call", {"name": "tenant_capabilities_list", "arguments": {}}))["grantedConsumables"]
            g_grants = content(mcp(go_url, "tools/call", {"name": "tenant_capabilities_list", "arguments": {}}))["grantedConsumables"]
            equivalent("tenant_capabilities_list granted consumables", p_grants, g_grants)
            p = mcp(python_url, "resources/list", {})
            g = mcp(go_url, "resources/list", {})
            equivalent("resources/list", sorted(p["resources"], key=lambda item: item["uri"]), sorted(g["resources"], key=lambda item: item["uri"]))
            for uri in ["re8ch://tenant/acme/catalog", "re8ch://tenant/acme/instances", "re8ch://tenant/acme/bindings", "re8ch://tenant/acme/service-usage", "re8ch://tenant/acme/claims", "re8ch://tenant/acme/grafana/capabilities", "re8ch://tenant/acme/byoc/registrations", "re8ch://tenant/acme/byoc/connections"]:
                p = mcp(python_url, "resources/read", {"uri": uri})
                g = mcp(go_url, "resources/read", {"uri": uri})
                equivalent("resources/read " + uri, json.loads(p["contents"][0]["text"]), json.loads(g["contents"][0]["text"]))
            for path in ["/billing/usage?from=2026-09-30T00:00:00Z&to=2026-10-01T00:00:00Z", "/.well-known/oauth-protected-resource/mcp"]:
                equivalent("HTTP " + path, request(python_url + path), request(go_url + path))
            exchanged = [token_exchange(base) for base in (python_url, go_url)]
            for value in exchanged:
                equivalent("OAuth preserves user token", value["access_token"], TOKEN)
            equivalent("OAuth response fields", {k: v for k, v in exchanged[0].items() if k != "expires_in"}, {k: v for k, v in exchanged[1].items() if k != "expires_in"})
            if abs(exchanged[0]["expires_in"] - exchanged[1]["expires_in"]) > 1:
                raise AssertionError("OAuth token lifetime differs")
            print("PASS OAuth token lifetime")
            unsupported = {"name": "proposed", "spec": {"serviceClass": "database.postgresql.shared.v1", "planRef": "standard", "regionRef": "loudi"}}
            for name, args in [("service_instance_plan", unsupported), ("service_instance_create", {**unsupported, "idempotency_key": "fixture-create-1"})]:
                for base in (python_url, go_url):
                    status, result = request(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
                    error_text = result.get("error", {}).get("message", "")
                    tool_result = result.get("result", {})
                    if tool_result.get("isError"):
                        error_text = " ".join(item.get("text", "") for item in tool_result.get("content", []))
                    if status != 200 or "no live provisioner" not in error_text:
                        raise AssertionError(f"{base} falsely accepted an intent-only service: {status} {result}")
                    print("PASS intent-only service rejected", base, name)
            invalid_headers = {"Authorization": "Bearer invalid"}
            for base in (python_url, go_url):
                status, _ = request(base + "/billing/usage", headers=invalid_headers)
                equivalent("invalid billing bearer " + base, status, 401)
                status, _ = request(base + "/billing/usage", headers={"X-RE8CH-Tenant": "other"})
                equivalent("cross-tenant billing " + base, status, 403)
            draft = content(mcp(go_url, "tools/call", {"name": "dynamic_skill_submit", "arguments": {"name": "special-order", "description": "Process a special order", "workflow": "Read the approved service plan before acting."}}))
            equivalent("dynamic skill starts pending", draft["status"], "Pending")
            search = content(mcp(go_url, "tools/call", {"name": "dynamic_skill_search", "arguments": {"query": "special order"}}))
            equivalent("pending skill hidden", search["matches"], [])
            status, published = request(f"http://127.0.0.1:{admin_port}/internal/dynamic-skills", "POST", {"action": "review", "skillRef": draft["skillRef"], "publish": True}, {"Authorization": "Bearer fixture-admin-token", "X-RE8CH-Admin-Subject": "reviewer-1"})
            equivalent("admin review", (status, published["status"]), (200, "Published"))
            search = content(mcp(go_url, "tools/call", {"name": "dynamic_skill_search", "arguments": {"query": "special order"}}))
            equivalent("published skill searchable", search["matches"][0]["skillRef"], draft["skillRef"])
            loaded = content(mcp(go_url, "tools/call", {"name": "dynamic_skill_load", "arguments": {"skillRef": draft["skillRef"], "instruction": "Handle order 42"}}))
            equivalent("published skill loaded", (loaded["skill"]["workflow"], loaded["task"]), ("Read the approved service plan before acting.", "Handle order 42"))
        finally:
            process.terminate()
            process.wait(timeout=5)
            python_server.shutdown()
            fixture.shutdown()


if __name__ == "__main__":
    main()
