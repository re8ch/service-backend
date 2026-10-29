import json
import subprocess
from pathlib import Path

import pytest

from runtime import CloudPlatform, Inventory, OperationError, OperationRunner, redact


ROOT = Path(__file__).parents[1]
NODE_FIXTURE = ROOT / "tests/fixtures/synthetic-nodes.yaml"


def completed(stdout="{}", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_inventory_loads_only_the_explicit_synthetic_fixture():
    inventory = Inventory([NODE_FIXTURE])
    k3s = {name for name, item in inventory.targets.items() if any(capability.startswith("k3s-") for capability in item["spec"].get("capabilities", []))}
    assert k3s == {"test-primary", "test-secondary"}


def test_synthetic_active_node_has_scheduling_inventory():
    inventory = Inventory([NODE_FIXTURE])
    active = [item for item in inventory.targets.values() if item["spec"].get("cluster", {}).get("membership") == "active"]

    assert len(active) == 1
    for item in active:
        cluster = item["spec"]["cluster"]
        assert cluster["roles"]
        assert cluster["services"]
        assert cluster["parameters"]["cpu"] > 0
        assert cluster["parameters"]["memoryKi"] > 0
        assert cluster["parameters"]["ephemeralStorageKi"] > 0
        if "ceph-csi" in cluster["services"]:
            assert cluster["storage"]["csiDrivers"]


def test_plaintext_production_node_inventory_is_absent():
    assert not (ROOT / "inventory/nodes.yaml").exists()


def test_redaction_is_recursive():
    assert redact({"nested": {"token": "never", "ok": 1}}) == {"nested": {"token": "[REDACTED]", "ok": 1}}


def test_secret_values_are_never_returned():
    payload = {"items": [{"metadata": {"name": "x"}, "data": {"password": "eA=="}}]}
    runner = OperationRunner(lambda *args, **kwargs: completed(json.dumps(payload)))
    service = CloudPlatform(Inventory([NODE_FIXTURE]), runner, kubeconfig="x", ssh_config="y")
    result = service.kube_simple("secrets", namespace="default")
    assert "data" not in result["result"]["items"][0]
    assert "eA==" not in json.dumps(result)


def test_ssh_rejects_empty_command():
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError, match="argv must not be empty"):
        service.node_ssh_exec("test-primary", [])


def test_apply_rejects_inline_secret_material():
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as caught:
        service.kube_resource("apply", "secret", manifest={"data": {"x": "eA=="}})
    assert caught.value.code == "SECRET_VALUE_FORBIDDEN"


def test_apply_uses_manifest_without_positional_resource():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed("applied")

    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(run), kubeconfig="x", ssh_config="y")
    service.kube_resource("apply", "networkpolicies", namespace="opensandbox-system", manifest={
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "test", "namespace": "opensandbox-system"},
        "spec": {},
    })
    assert calls
    assert "networkpolicies" not in calls[0][0]
    assert "-f" in calls[0][0]


def test_patch_preserves_version_precondition_without_invalid_cli_flag():
    calls = []
    service = CloudPlatform(Inventory([NODE_FIXTURE]),
                            OperationRunner(lambda argv, **kwargs: calls.append(argv) or completed()),
                            kubeconfig="x", ssh_config="y")
    manifest = {"spec": {"replicas": 1}}
    service.kube_resource("patch", "deployments", name="test", namespace="default",
                          manifest=manifest, resource_version="123")
    argv = calls[0]
    assert not any(arg.startswith("--resource-version") for arg in argv)
    assert json.loads(argv[argv.index("-p") + 1]) == {
        "metadata": {"resourceVersion": "123"}, "spec": {"replicas": 1}}
    assert "metadata" not in manifest
    with pytest.raises(OperationError) as caught:
        service.kube_resource("patch", "deployments", name="test",
                              manifest={"metadata": {"resourceVersion": "124"}},
                              resource_version="123")
    assert caught.value.code == "RESOURCE_VERSION_CONFLICT"
    assert len(calls) == 1


def test_ssh_preserves_posix_arguments_through_remote_shell():
    calls = []
    def remote_shell(argv, **kwargs):
        calls.append(argv)
        assert argv[:5] == ["ssh", "-F", "y", "--", "test-primary.invalid"]
        return subprocess.run(["sh", "-c", " ".join(argv[5:])], **kwargs)
    service = CloudPlatform(Inventory([NODE_FIXTURE]),
                            OperationRunner(remote_shell), kubeconfig="x", ssh_config="y")
    arguments = ["a b", "$(false)", "semi;colon", "quote'and\"", ""]
    result = service.node_ssh_exec("test-primary", ["printf", "%s\\n", *arguments])
    assert result["exitCode"] == 0
    assert result["stdout"].splitlines() == arguments


def test_ssh_uses_inventory_address_for_reachability():
    calls = []
    service = CloudPlatform(Inventory([NODE_FIXTURE]),
                            OperationRunner(lambda argv, **kwargs: calls.append(argv) or completed()),
                            kubeconfig="x", ssh_config="y")
    service.node_reachability("test-secondary")
    assert calls == [["ssh", "-F", "y", "--", "test-secondary.invalid", "hostname"]]


def test_webmail_credential_preview_does_not_access_cluster():
    def fail(*args, **kwargs):
        pytest.fail("preview must not call Kubernetes")
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(fail), kubeconfig="x", ssh_config="y")
    assert service.ensure_webmail_session_secret()["applied"] is False


@pytest.mark.parametrize("initial,create_code,final,created", [
    ("present", None, None, False),
    ("", 0, "present", True),
    ("", 1, "present", False),
])
def test_webmail_credential_create_preserves_existing_and_concurrent_keys(initial, create_code, final, created):
    calls = []
    statuses = iter([initial, final])
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "get" in argv:
            return completed(next(statuses))
        manifest = json.loads(kwargs["input"])
        assert manifest["metadata"] == {"namespace": "mail-system", "name": "stalwart-webmail-session"}
        value = manifest["stringData"]["session-secret"]
        assert len(value) == 64 and int(value, 16) >= 0
        assert "create" in argv and "apply" not in argv
        return completed("secret/stalwart-webmail-session", create_code)
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(run), kubeconfig="x", ssh_config="y")
    result = service.ensure_webmail_session_secret(apply=True)
    assert result["created"] is created
    for _, kwargs in calls:
        if kwargs.get("input"):
            assert json.loads(kwargs["input"])["stringData"]["session-secret"] not in json.dumps(result)


def test_webmail_credential_rejects_invalid_existing_key_without_rotation():
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(lambda *a, **kw: completed("invalid")), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as exc:
        service.ensure_webmail_session_secret(apply=True)
    assert exc.value.code == "CREDENTIAL_INVALID"


def test_webmail_credential_create_failure_does_not_return_payload():
    def run(argv, **kwargs):
        if "get" in argv:
            return completed("")
        return subprocess.CompletedProcess([], 1, kwargs["input"], kwargs["input"])
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(run), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as exc:
        service.ensure_webmail_session_secret(apply=True)
    assert str(exc.value) == "Webmail credential creation failed"


def test_webmail_credential_tool_is_admin_only_and_rejects_target_overrides():
    import importlib.util
    from runtime import Identity
    spec = importlib.util.spec_from_file_location("cloud_platform_server_broker_test", ROOT / "operations/cloud-platform/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as exc:
        server.dispatch(service, "credentials.webmail-session.ensure", {}, Identity("reader", ()))
    assert exc.value.code == "AUTHORIZATION_DENIED"
    admin = Identity("operator", (server.ADMIN_GROUP,))
    for args in ({"namespace": "other"}, {"apply": "true"}, {"values": {"x": "secret"}}):
        with pytest.raises(OperationError) as exc:
            server.dispatch(service, "credentials.webmail-session.ensure", args, admin)
        assert exc.value.code == "INVALID_ARGUMENT"
    assert server.dispatch(service, "credentials.webmail-session.ensure", {}, admin)["applied"] is False
