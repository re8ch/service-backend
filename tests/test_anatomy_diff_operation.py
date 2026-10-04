import importlib.util
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/cloud-platform"))
import anatomy_diff_operation as diff
import plugin_capabilities
from runtime import Identity, OperationError


def response(status, body=None):
    return {"status": status, "body": json.dumps(body or {}).encode(),
            "headers": {"content-type": ["application/json"]}}


class Service:
    def __init__(self):
        self.calls = []
        self.object = None

    def _plugin_http_get(self, path):
        self.calls.append(("GET", path))
        if path.endswith("/runtimecommits/base") or path.endswith("/runtimecommits/target"):
            name = path.rsplit("/", 1)[-1]
            return response(200, {"metadata": {"uid": name + "-uid", "resourceVersion": "5"},
                                  "status": {"phase": "Sealed"}})
        return response(200, self.object) if self.object else response(404)

    def _plugin_http_request(self, method, path, body):
        self.calls.append((method, path))
        self.object = json.loads(body)
        self.object["metadata"].update({"uid": "new-uid", "resourceVersion": "1"})
        self.object["status"] = {"phase": "Ready", "added": [], "removed": [], "modified": []}
        return response(201, self.object)


ARGS = {"phase": "plan", "namespace": diff.NAMESPACE, "name": "diff-case",
        "baseCommit": "base", "targetCommit": "target", "idempotencyKey": "fixture-key-123"}


def test_plan_execute_and_idempotent_recovery(monkeypatch):
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": diff.SCHEMA_HASH})
    service = Service()
    plan = diff.call(service, ARGS)
    assert plan["state"] == "ready"
    assert not any(method == "POST" for method, _ in service.calls)
    created = diff.call(service, {**ARGS, "phase": "execute", "planHash": plan["planHash"]})
    assert created["state"] == "created" and created["httpStatus"] == 201
    assert service.object["spec"] == {"baseRef": {"name": "base"}, "targetRef": {"name": "target"}}
    assert diff.call(service, {**ARGS, "phase": "status"})["result"]["uid"] == "new-uid"
    assert diff.call(service, {**ARGS, "phase": "execute", "planHash": plan["planHash"]})["state"] == "already_created"
    assert len([call for call in service.calls if call[0] == "POST"]) == 1


def test_rejects_changed_plan_and_wrong_namespace(monkeypatch):
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": diff.SCHEMA_HASH})
    service = Service()
    with pytest.raises(OperationError) as changed:
        diff.call(service, {**ARGS, "phase": "execute", "planHash": "0" * 64})
    assert changed.value.code == "PLUGIN_PRECONDITION_FAILED"
    with pytest.raises(OperationError):
        diff.call(service, {**ARGS, "namespace": "production"})
    assert not any(method == "POST" for method, _ in service.calls)


def test_tool_only_visible_to_admin():
    spec = importlib.util.spec_from_file_location("anatomy_server_test", ROOT / "operations/cloud-platform/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    viewer = Identity("viewer", ())
    admin = Identity("admin", (server.ADMIN_GROUP,))
    assert diff.TOOL["name"] not in {tool["name"] for tool in server.tool_list(viewer)}
    assert diff.TOOL["name"] in {tool["name"] for tool in server.tool_list(admin)}
    with pytest.raises(OperationError):
        server.dispatch(Service(), diff.TOOL["name"], ARGS, viewer)


def test_native_post_transport_is_bound_to_one_path(tmp_path):
    if not shutil.which("kubectl"):
        pytest.skip("kubectl unavailable")
    from plugin_http_transport import request

    received = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            payload = b'{"metadata":{"name":"diff-case"}}'
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    api = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    kubeconfig = tmp_path / "fixture.yaml"
    kubeconfig.write_text(json.dumps({"apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "fixture", "cluster": {"server": f"http://127.0.0.1:{api.server_port}"}}],
        "contexts": [{"name": "fixture", "context": {"cluster": "fixture"}}],
        "current-context": "fixture"}))
    try:
        payload = b'{"kind":"RuntimeDiff"}'
        actual = request(str(kubeconfig), "POST", "/apis/anatomy.re8ch.com/v1alpha1/namespaces/runtime-anatomy-canary/runtimediffs", payload)
        assert actual["status"] == 201
        assert received[0][1] == payload
        assert received[0][0].endswith("/runtimediffs")
    finally:
        api.shutdown()
        thread.join(timeout=2)
