import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/cloud-platform"))
import cilium_cidr_operation as cidr
import plugin_capabilities
from runtime import Identity, OperationError

ARGS = {"phase": "plan", "name": "tool-cidr-canary-deadbeef", "idempotencyKey": "canary-request-123"}


def response(status, body=None):
    return {"status": status, "body": json.dumps(body or {}).encode(),
            "headers": {"content-type": ["application/json"]}}


class Service:
    def __init__(self):
        self.calls = []
        self.object = None
        self.lose_create_response = False
        self.lose_delete_response = False

    def _plugin_http_get(self, path):
        self.calls.append(("GET", path))
        return response(200, self.object) if self.object else response(404)

    def _plugin_http_request(self, method, path, body):
        self.calls.append((method, path))
        if method == "POST":
            self.object = json.loads(body)
            self.object["metadata"].update(uid="canary-uid", resourceVersion="7")
            if self.lose_create_response:
                raise OperationError("PLUGIN_HTTP_UNAVAILABLE", "response lost")
            return response(201, self.object)
        assert method == "DELETE"
        assert json.loads(body)["preconditions"] == {"uid": "canary-uid", "resourceVersion": "7"}
        self.object = None
        if self.lose_delete_response:
            raise OperationError("PLUGIN_HTTP_UNAVAILABLE", "response lost")
        return response(200, {"apiVersion": "v1", "kind": "Status", "status": "Success", "code": 200})


@pytest.fixture(autouse=True)
def bound_schema(monkeypatch):
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": cidr.SCHEMA_HASH})


def test_create_plan_execute_status_and_delete_with_uid_preconditions():
    service = Service()
    plan = cidr.create(service, ARGS)
    assert plan["state"] == "ready" and plan["target"]["externalCIDRs"] == ["198.51.100.0/24"]
    assert not any(method == "POST" for method, _ in service.calls)
    created = cidr.create(service, {**ARGS, "phase": "execute", "planHash": plan["planHash"]})
    assert created["state"] == "created" and created["httpStatus"] == 201
    assert service.object["spec"] == {"externalCIDRs": ["198.51.100.0/24"]}
    assert cidr.create(service, {**ARGS, "phase": "status"})["result"]["uid"] == "canary-uid"
    assert cidr.create(service, {**ARGS, "phase": "execute", "planHash": plan["planHash"]})["state"] == "already_created"
    assert len([call for call in service.calls if call[0] == "POST"]) == 1
    delete_plan = cidr.delete(service, ARGS)
    assert delete_plan["target"] == {"name": ARGS["name"], "uid": "canary-uid", "resourceVersion": "7"}
    deleted = cidr.delete(service, {**ARGS, "phase": "execute", "planHash": delete_plan["planHash"]})
    assert deleted["state"] == "deleted" and deleted["httpStatus"] == 200
    assert cidr.delete(service, {**ARGS, "phase": "status"})["state"] == "absent"


def test_changed_plan_or_other_owner_never_mutates():
    service = Service()
    with pytest.raises(OperationError) as changed:
        cidr.create(service, {**ARGS, "phase": "execute", "planHash": "0" * 64})
    assert changed.value.code == "PLUGIN_PRECONDITION_FAILED"
    with pytest.raises(OperationError):
        cidr.create(service, {**ARGS, "name": "production-cidr-group"})
    assert not any(method == "POST" for method, _ in service.calls)
    service.object = cidr._body(ARGS)
    service.object["metadata"].update(uid="canary-uid", resourceVersion="7")
    service.object["metadata"]["annotations"][cidr.MARKER] = "another-owner"
    with pytest.raises(OperationError) as conflict:
        cidr.delete(service, ARGS)
    assert conflict.value.code == "PLUGIN_OPERATION_CONFLICT"
    assert not any(method == "DELETE" for method, _ in service.calls)


def test_response_loss_is_recovered_from_exact_owned_resource():
    service = Service()
    plan = cidr.create(service, ARGS)
    service.lose_create_response = True
    result = cidr.create(service, {**ARGS, "phase": "execute", "planHash": plan["planHash"]})
    assert result["state"] == "recovered" and result["result"]["uid"] == "canary-uid"
    delete_plan = cidr.delete(service, ARGS)
    service.lose_delete_response = True
    assert cidr.delete(service, {**ARGS, "phase": "execute", "planHash": delete_plan["planHash"]})["state"] == "recovered_absent"


def test_schema_drift_and_changed_resource_version_stop_write(monkeypatch):
    service = Service()
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": "changed"})
    with pytest.raises(OperationError) as drift:
        cidr.create(service, ARGS)
    assert drift.value.code == "PLUGIN_SCHEMA_CHANGED"
    assert not any(method == "POST" for method, _ in service.calls)
    monkeypatch.setattr(plugin_capabilities, "openapi", lambda _service, _group: {"sha256": cidr.SCHEMA_HASH})
    create_plan = cidr.create(service, ARGS)
    cidr.create(service, {**ARGS, "phase": "execute", "planHash": create_plan["planHash"]})
    delete_plan = cidr.delete(service, ARGS)
    service.object["metadata"]["resourceVersion"] = "8"
    with pytest.raises(OperationError) as changed:
        cidr.delete(service, {**ARGS, "phase": "execute", "planHash": delete_plan["planHash"]})
    assert changed.value.code == "PLUGIN_PRECONDITION_FAILED"
    assert not any(method == "DELETE" for method, _ in service.calls)


def test_admin_only_registration_and_dispatch():
    spec = importlib.util.spec_from_file_location("cilium_server_test", ROOT / "operations/cloud-platform/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = Service()
    viewer = Identity("viewer", ())
    admin = Identity("admin", (module.ADMIN_GROUP,))
    for tool in (cidr.TOOL, cidr.DELETE_TOOL):
        assert tool["name"] not in {item["name"] for item in module.tool_list(viewer)}
        assert [item["name"] for item in module.tool_list(admin)].count(tool["name"]) == 1
        with pytest.raises(OperationError) as denied:
            module.dispatch(service, tool["name"], ARGS, viewer)
        assert denied.value.code == "AUTHORIZATION_DENIED"
        for relative in ("platform/apps/tenant-mcp/platform-modules.json",
                         "clusters/re8ch/charts/birfucate-metering/files/tenant-platform-modules.json"):
            assert json.loads((ROOT / relative).read_text())["backends"]["native"]["allowed"][tool["name"]]
    assert not service.calls
