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


def test_apply_allows_configmap_data():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed("applied")

    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(run), kubeconfig="x", ssh_config="y")
    service.kube_resource("apply", "configmaps", namespace="default", manifest={
        "apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "migration"},
        "data": {"migration.sql": "select 1;"},
    })
    assert json.loads(calls[0][1]["input"])["data"] == {"migration.sql": "select 1;"}


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


def authentication_git_authority(monkeypatch, commit_sha):
    reconciler = """apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: re8ch-authentication-config-reconciler
  namespace: kube-system
  labels:
    app.kubernetes.io/name: re8ch-authentication-config-reconciler
    app.kubernetes.io/component: control-plane
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: re8ch-authentication-config-reconciler
  template:
    metadata:
      labels:
        app.kubernetes.io/name: re8ch-authentication-config-reconciler
    spec:
      containers: [{name: reconciler, image: example.invalid/reconciler@sha256:deadbeef}]
"""

    def read(url, *, json_body=False, limit=1_048_576):
        del limit
        if url.endswith("/commits/main"):
            assert json_body
            return {"sha": commit_sha, "commit": {"verification": {"verified": True}}}
        if url.endswith("/dex-dual-issuer-authentication.yaml"):
            return "apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
        if url.endswith("/authentication-config-reconciler.yaml"):
            return reconciler
        raise AssertionError(url)

    monkeypatch.setattr(CloudPlatform, "_github_read", staticmethod(read))


def test_authentication_reconcile_plans_with_server_dry_run(monkeypatch):
    commit_sha = "a" * 40
    authentication_git_authority(monkeypatch, commit_sha)
    calls = []
    service = CloudPlatform(
        Inventory([NODE_FIXTURE]),
        OperationRunner(lambda argv, **kwargs: calls.append((argv, kwargs)) or completed("configured")),
        kubeconfig="x",
        ssh_config="y",
    )

    result = service.reconcile_control_plane_authentication_config(commit_sha)

    assert result["applied"] is False
    assert result["restartRequested"] is False
    assert result["offlineNodesTolerated"] is True
    assert len(result["planHash"]) == 64
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert "--dry-run=server" in argv
    assert "restart" not in argv
    assert "kind: ConfigMap" in kwargs["input"]
    assert "kind: DaemonSet" in kwargs["input"]


def test_authentication_reconcile_requires_exact_plan_and_tolerates_offline_nodes(monkeypatch):
    commit_sha = "b" * 40
    authentication_git_authority(monkeypatch, commit_sha)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "daemonset" in argv:
            return completed(json.dumps({"status": {
                "desiredNumberScheduled": 3,
                "currentNumberScheduled": 2,
                "numberReady": 2,
                "numberAvailable": 2,
                "numberUnavailable": 1,
            }}))
        if "--raw=/livez" in argv:
            return completed("ok")
        return completed("configured")

    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(run), kubeconfig="x", ssh_config="y")
    plan = service.reconcile_control_plane_authentication_config(commit_sha)
    calls.clear()
    with pytest.raises(OperationError) as caught:
        service.reconcile_control_plane_authentication_config(commit_sha, apply=True, plan_hash="0" * 64)
    assert caught.value.code == "PLAN_HASH_MISMATCH"
    assert not calls

    result = service.reconcile_control_plane_authentication_config(commit_sha, apply=True, plan_hash=plan["planHash"])

    assert result["applied"] is True
    assert result["apiLive"] is True
    assert result["daemonSet"]["numberUnavailable"] == 1
    assert result["offlineNodesTolerated"] is True
    assert all("restart" not in argv for argv, _ in calls)
    assert "--dry-run=server" not in calls[0][0]


def test_authentication_reconcile_rejects_unverified_main(monkeypatch):
    commit_sha = "c" * 40
    monkeypatch.setattr(CloudPlatform, "_github_read", staticmethod(
        lambda *_args, **_kwargs: {"sha": commit_sha, "commit": {"verification": {"verified": False}}}
    ))
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as caught:
        service.reconcile_control_plane_authentication_config(commit_sha)
    assert caught.value.code == "UNREVIEWED_REVISION"


def test_authentication_reconcile_tool_is_admin_only_and_bounded(monkeypatch):
    import importlib.util
    from runtime import Identity

    commit_sha = "d" * 40
    authentication_git_authority(monkeypatch, commit_sha)
    spec = importlib.util.spec_from_file_location("cloud_platform_server_auth_reconcile_test", ROOT / "operations/cloud-platform/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    service = CloudPlatform(Inventory([NODE_FIXTURE]), OperationRunner(lambda *a, **kw: completed("configured")), kubeconfig="x", ssh_config="y")
    with pytest.raises(OperationError) as caught:
        server.dispatch(service, "reconcile_control_plane_authentication_config", {"commitSha": commit_sha}, Identity("reader", ()))
    assert caught.value.code == "AUTHORIZATION_DENIED"
    admin = Identity("operator", (server.ADMIN_GROUP,))
    with pytest.raises(OperationError) as caught:
        server.dispatch(service, "reconcile_control_plane_authentication_config", {"commitSha": commit_sha, "namespace": "default"}, admin)
    assert caught.value.code == "INVALID_ARGUMENT"
    assert server.dispatch(service, "reconcile_control_plane_authentication_config", {"commitSha": commit_sha}, admin)["applied"] is False
