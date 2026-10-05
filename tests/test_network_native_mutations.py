import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "operations/cloud-platform"))
import network_native_mutations as network
import plugin_capabilities
from runtime import Identity, OperationError


def test_manifest_covers_72_distinct_non_routeros_mutations():
    rows = network.build_manifest()
    assert rows == network.MANIFEST_ROWS
    assert len(rows) == len({row["name"] for row in rows}) == 72
    assert all(row["kind"] != "RouterOSNode" and not row["path"].endswith("/status") for row in rows)
    assert all(row["method"] in {"POST", "PUT", "PATCH", "DELETE"} for row in rows)
    assert {row["plugin"] for row in rows} == {"advfab", "advfab-legacy", "mobile-egress", "public-edge"}


def test_generated_tools_are_admin_only_and_route_to_adapter(monkeypatch):
    spec = importlib.util.spec_from_file_location("network_server_test", ROOT / "operations/cloud-platform/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    viewer = Identity("viewer", ())
    admin = Identity("admin", (server.ADMIN_GROUP,))
    name = network.TOOLS[0]["name"]
    assert name not in {item["name"] for item in server.tool_list(viewer)}
    assert [item["name"] for item in server.tool_list(admin)].count(name) == 1
    class Service: pass
    with pytest.raises(OperationError) as denied:
        server.dispatch(Service(), name, {}, viewer)
    assert denied.value.code == "AUTHORIZATION_DENIED"
    monkeypatch.setattr(network, "call", lambda _service, tool, args: {"tool": tool, "args": args})
    assert server.dispatch(Service(), name, {}, admin)["tool"] == name


def test_create_plan_uses_fixed_native_contract_and_requires_hash(monkeypatch):
    tool = next(row for row in network.TOOLS if row["operationId"] == "createNetworkingAdvfabOrgV1alpha1NetworkIntervention")
    args = {"phase": "plan", "name": "tool-canary", "idempotencyKey": "request-123", "body": {
        "apiVersion": "networking.advfab.org/v1alpha1", "kind": "NetworkIntervention",
        "metadata": {"name": "tool-canary"},
        "spec": {"subjectRef": {}, "cause": "Gateway", "detectedAt": "2026-10-05T00:00:00Z", "fingerprint": "test-12345"}}}
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": tool["schemaHash"]})
    class Service:
        def _plugin_http_get(self, path):
            assert path.endswith("/tool-canary")
            return {"status": 404}
        def _plugin_http_request(self, *_args):
            raise AssertionError("plan must not write")
    def planner(_argv, input, capture_output, timeout):
        request = json.loads(input)
        assert request["tool"] == tool["name"]
        request["body"]["metadata"]["annotations"] = {"operations.re8ch.com/idempotency-hash": hashlib.sha256(b"request-123").hexdigest()}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({
            "method": "POST", "path": tool["path"], "body": request["body"], "planHash": "a" * 64}).encode()})()
    monkeypatch.setattr(network.subprocess, "run", planner)
    plan = network.call(Service(), tool["name"], args)
    assert plan["state"] == "ready" and plan["planHash"] == "a" * 64
    with pytest.raises(OperationError) as changed:
        network.call(Service(), tool["name"], {**args, "phase": "execute", "planHash": "b" * 64})
    assert changed.value.code == "PLUGIN_PRECONDITION_FAILED"


def test_create_execute_checks_http_response_and_observed_effect(monkeypatch):
    tool = next(row for row in network.TOOLS if row["operationId"] == "createNetworkingAdvfabOrgV1alpha1NetworkIntervention")
    body = {"apiVersion": "networking.advfab.org/v1alpha1", "kind": "NetworkIntervention",
            "metadata": {"name": "tool-canary"},
            "spec": {"subjectRef": {}, "cause": "Gateway", "detectedAt": "2026-10-05T00:00:00Z", "fingerprint": "test-12345"}}
    args = {"phase": "execute", "name": "tool-canary", "idempotencyKey": "request-123", "planHash": "a" * 64, "body": body}
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": tool["schemaHash"]})
    def planner(_argv, input, capture_output, timeout):
        request = json.loads(input)
        request["body"]["metadata"]["annotations"] = {"operations.re8ch.com/idempotency-hash": hashlib.sha256(b"request-123").hexdigest()}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({
            "method": "POST", "path": tool["path"], "body": request["body"], "planHash": "a" * 64}).encode()})()
    monkeypatch.setattr(network.subprocess, "run", planner)
    class Service:
        object = None
        def _plugin_http_get(self, _path):
            return {"status": 200, "body": json.dumps(self.object).encode()} if self.object else {"status": 404}
        def _plugin_http_request(self, method, path, raw_body, content_type):
            assert (method, path, content_type) == ("POST", tool["path"], "application/json")
            self.object = json.loads(raw_body)
            self.object["metadata"].update(uid="uid-1", resourceVersion="7")
            return {"status": 201, "headers": {"content-type": ["application/json"]}, "body": json.dumps(self.object).encode()}
    service = Service()
    result = network.call(service, tool["name"], args)
    assert result["state"] == "observed" and result["httpStatus"] == 201
    assert result["target"]["uid"] == "uid-1"
    assert network.call(service, tool["name"], args)["state"] == "already_created"
