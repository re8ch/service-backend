import datetime as dt
import importlib.util
import io
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/releases"))
from unified_bundle import canonical, compile_bundle, deterministic_archive, digest

SPEC = importlib.util.spec_from_file_location("ssa_executor", ROOT / "operations/releases/ssa_executor.py")
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)

MATERIALIZE_SPEC = importlib.util.spec_from_file_location(
    "operation_materialize", ROOT / "operations/releases/operation_materialize.py")
materialize = importlib.util.module_from_spec(MATERIALIZE_SPEC)
MATERIALIZE_SPEC.loader.exec_module(materialize)

NOW = dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc)


def contract(inventory=None, deletion=None):
    inventory = inventory or ["apps/Deployment/demo/demo"]
    return {
        "apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "ReleaseContract",
        "metadata": {"name": "demo"},
        "spec": {
            "target": {"cluster": "re8ch", "namespace": "demo"},
            "source": {"renderer": "kustomize", "revision": "a" * 40},
            "provenance": {
                "repository": "re8ch/service-backend", "workflow": ".github/workflows/release.yml",
                "ref": "refs/heads/main", "environment": "production",
                "audience": "https://kube-api.re8ch.com/release-executor",
                "signer": "https://github.com/re8ch/service-backend/.github/workflows/release.yml@refs/heads/main",
                "issuer": "https://token.actions.githubusercontent.com",
            },
            "authorization": {"fieldManager": "re8ch-release/demo", "resources": ["apps/Deployment"], "objectNames": ["demo"]},
            "inventory": {"digest": digest(canonical(inventory))},
            "lifecycle": {"prune": True, "healthTimeoutSeconds": 1, "protectedKinds": [], "deletionAllowlist": deletion or []},
        },
    }


def rendered(name="demo"):
    return f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: demo}}
spec:
  replicas: 1
  template:
    spec:
      containers:
      - {{name: demo, image: 'registry.example/demo@sha256:{'a' * 64}'}}
""".encode()


def prepare(tmp_path, sequence=1, operation_id=None, previous=None):
    value = contract()
    archive, manifest = compile_bundle(value, rendered())
    bundle_path = tmp_path / "bundle.tar.gz"
    bundle_path.write_bytes(archive)
    operation = {
        "apiVersion": "delivery.re8ch.com/release-operation/v1alpha1",
        "operationId": operation_id or str(uuid.uuid4()), "sequence": sequence,
        "action": "reconcile", "idempotencyKey": "0123456789abcdef",
        "releaseId": "demo", "target": "re8ch/demo", "contractDigest": digest(canonical(value)),
        "bundle": {"reference": "oci://registry.example/releases/demo", "digest": digest(archive),
                   "payloadDigest": digest(archive),
                   "signer": "https://github.com/re8ch/service-backend/.github/workflows/release.yml@refs/heads/main",
                   "issuer": "https://token.actions.githubusercontent.com"},
        "inventoryDigest": manifest["inventoryDigest"],
        "github": {"repository": "re8ch/service-backend", "workflow": ".github/workflows/release.yml",
                   "ref": "refs/heads/main", "commit": "b" * 40, "runId": "123", "environment": "production",
                   "audience": "https://kube-api.re8ch.com/release-executor"},
        "issuedAt": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "expiresAt": (NOW + dt.timedelta(minutes=10)).isoformat(), "nonce": "0123456789abcdef",
    }
    if previous:
        operation["previousApprovedDigest"] = previous
    return value, operation, bundle_path


def staged_release(tmp_path, suffix, deployment_version, config_value, revision="a" * 40):
    documents = f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: demo, namespace: demo, labels: {{drill: '{deployment_version}'}}}}
spec:
  replicas: 1
  template:
    spec:
      containers:
      - {{name: demo, image: 'registry.example/demo@sha256:{'a' * 64}'}}
---
apiVersion: v1
kind: ConfigMap
metadata: {{name: demo-config, namespace: demo}}
data: {{value: '{config_value}'}}
""".encode()
    inventory = ["apps/Deployment/demo/demo", "core/ConfigMap/demo/demo-config"]
    value = contract(inventory)
    value["spec"]["source"]["revision"] = revision
    value["spec"]["authorization"]["resources"] = ["apps/Deployment", "core/ConfigMap"]
    value["spec"]["authorization"]["objectNames"] = ["demo", "demo-config"]
    value["spec"]["lifecycle"]["stages"] = [
        {"name": "runtime", "kinds": ["Deployment"]},
        {"name": "custom-resources", "kinds": ["ConfigMap"]},
    ]
    archive, manifest = compile_bundle(value, documents)
    path = tmp_path / f"{suffix}.tar.gz"
    path.write_bytes(archive)
    return value, archive, manifest, path


def operation_for(value, archive, manifest, sequence, previous=None, action="reconcile"):
    operation = {
        "apiVersion": "delivery.re8ch.com/release-operation/v1alpha1",
        "operationId": str(uuid.uuid4()), "sequence": sequence, "action": action,
        "idempotencyKey": uuid.uuid4().hex, "releaseId": "demo", "target": "re8ch/demo",
        "contractDigest": digest(canonical(value)),
        "bundle": {"reference": "oci://registry.example/releases/demo", "digest": digest(archive),
                   "payloadDigest": digest(archive),
                   "signer": "https://github.com/re8ch/service-backend/.github/workflows/release.yml@refs/heads/main",
                   "issuer": "https://token.actions.githubusercontent.com"},
        "inventoryDigest": manifest["inventoryDigest"],
        "github": {"repository": "re8ch/service-backend", "workflow": ".github/workflows/release.yml",
                   "ref": "refs/heads/main", "commit": "b" * 40, "runId": "123", "environment": "production",
                   "audience": "https://kube-api.re8ch.com/release-executor"},
        "issuedAt": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "expiresAt": (NOW + dt.timedelta(minutes=10)).isoformat(), "nonce": uuid.uuid4().hex,
    }
    if previous:
        operation["previousApprovedDigest"] = previous
    if action == "rollback":
        operation["approvalRef"] = "change/ceph-rollback-drill"
    return operation


class Runner:
    def __init__(self, conflict=False, live=None, pulled_bundle=None):
        self.calls = []
        self.conflict = conflict
        self.live = live
        self.pulled_bundle = pulled_bundle

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get("input")))
        if self.conflict and "--dry-run=server" in argv and "--force-conflicts" not in argv:
            return subprocess.CompletedProcess(argv, 1, "", "field manager conflict")
        if argv[:2] == ["kubectl", "get"]:
            body = self.live or {"metadata": {"generation": 2}, "spec": {"replicas": 1},
                                 "status": {"observedGeneration": 2, "availableReplicas": 1}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        if argv[:3] == ["flux", "pull", "artifact"] and self.pulled_bundle:
            Path(argv[argv.index("--output") + 1], "release.bundle").write_bytes(self.pulled_bundle)
        return subprocess.CompletedProcess(argv, 0, "ok", "")


def test_reconcile_dry_runs_applies_persists_and_is_idempotent(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    result = instance.reconcile(value, operation, bundle_path, tmp_path / "state", verify_signature=False)
    assert result["outcome"] == "healthy"
    apply_calls = [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert len(apply_calls) == 2
    assert "--dry-run=server" in apply_calls[0][0]
    assert "--field-manager=re8ch-release/demo" in apply_calls[1][0]
    persisted = json.loads((tmp_path / "state/state/demo.json").read_text())
    assert persisted["inventory"] == ["apps/Deployment/demo/demo"]
    assert persisted["bundleReference"] == "oci://registry.example/releases/demo"
    assert persisted["payloadDigest"] == operation["bundle"]["payloadDigest"]
    count = len(runner.calls)
    assert instance.reconcile(value, operation, bundle_path, tmp_path / "state", verify_signature=False) == result
    assert len(runner.calls) == count


@pytest.mark.parametrize("fault", [None, "payload", "inventory", "empty-stages"])
def test_signed_legacy_bundle_replays_without_repacking_or_skipping_validation(tmp_path, fault):
    from jsonschema import ValidationError
    value, operation, bundle_path = prepare(tmp_path)
    manifest, objects = executor.load_archive(bundle_path)
    del manifest["stages"]
    if fault == "inventory":
        manifest["inventoryDigest"] = "sha256:" + "0" * 64
    if fault == "empty-stages":
        manifest["stages"] = []
    legacy = deterministic_archive({"manifest.json": canonical(manifest), "objects.json": canonical(objects)})
    bundle_path.write_bytes(legacy)
    operation["bundle"]["digest"] = digest(legacy)
    operation["bundle"]["payloadDigest"] = digest(legacy)
    if fault == "payload":
        operation["bundle"]["payloadDigest"] = "sha256:" + "0" * 64
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    if fault:
        with pytest.raises((executor.Rejected, ValidationError)):
            instance.reconcile(value, operation, bundle_path, tmp_path / "state")
        assert not [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
        return
    result = instance.reconcile(value, operation, bundle_path, tmp_path / "state")
    assert result["outcome"] == "healthy"
    assert result["observedDigest"] == digest(legacy)
    assert bundle_path.read_bytes() == legacy
    assert any(call[0][:2] == ["cosign", "verify"] for call in runner.calls)
    applies = [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert len(applies) == 2
    assert "--dry-run=server" in applies[0][0]
    assert json.loads(applies[1][1])["metadata"]["name"] == "demo"


def test_explicit_empty_stage_selection_finalizes_without_applying_objects(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner()
    result = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None).reconcile(
        value, operation, bundle_path, tmp_path / "state", verify_signature=False, stages=set(), finalize=True)
    assert result["outcome"] == "healthy"
    assert not [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    persisted = json.loads((tmp_path / "state/state/demo.json").read_text())
    assert persisted["inventory"] == ["apps/Deployment/demo/demo"]


@pytest.mark.parametrize("fault", [None, "fromContractDigest", "toContractDigest", "bundleReference", "bundleDigest", "unhealthy", "payload", "no-state", "force"])
def test_contract_migration_reuses_only_reviewed_accepted_artifact(tmp_path, monkeypatch, fault):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    instance.reconcile(value, operation, bundle_path, tmp_path / "state")
    old_contract_digest = digest(canonical(value))
    value["spec"]["provenance"]["environment"] = "domain-production"
    replay = json.loads(json.dumps(operation))
    replay.update(operationId=str(uuid.uuid4()), sequence=2,
                  previousApprovedDigest=operation["bundle"]["digest"], contractDigest=digest(canonical(value)))
    replay["github"]["environment"] = "domain-production"
    approved = {"fromContractDigest": old_contract_digest, "toContractDigest": replay["contractDigest"],
                "bundleReference": operation["bundle"]["reference"], "bundleDigest": operation["bundle"]["digest"]}
    if fault in approved:
        approved[fault] = "not-approved"
    policy = tmp_path / "migrations.json"
    policy.write_text(json.dumps({"demo": approved}))
    monkeypatch.setattr(executor, "CONTRACT_MIGRATIONS", policy)
    store = executor.Store(tmp_path / "state", "demo")
    state = store.state()
    if fault == "unhealthy":
        state["healthyDigest"] = "sha256:" + "0" * 64
        store.accept(state)
    if fault == "payload":
        state["payloadDigest"] = "sha256:" + "0" * 64
        store.accept(state)
    if fault == "no-state":
        replay["sequence"] = 1
        (tmp_path / "state/state/demo.json").unlink()
    if fault == "force":
        replay["action"] = "force-reconcile"
    runner.calls.clear()
    if fault:
        with pytest.raises(executor.Rejected, match="another contract"):
            instance.reconcile(value, replay, bundle_path, tmp_path / "state")
        assert not [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    else:
        result = instance.reconcile(value, replay, bundle_path, tmp_path / "state")
        assert result["observedDigest"] == operation["bundle"]["digest"]
        assert result["outcome"] == "healthy"
        assert store.state()["sequence"] == 2


@pytest.mark.parametrize("release_id", sorted(json.loads(executor.CONTRACT_MIGRATIONS.read_text())))
def test_accepted_migration_only_changes_reviewed_domain_provenance(release_id):
    import yaml
    value = yaml.safe_load((ROOT / f"contracts/releases/main-domain/{release_id}.yaml").read_text())
    migration = json.loads(executor.CONTRACT_MIGRATIONS.read_text())[value["metadata"]["name"]]
    assert digest(canonical(value)) == migration["toContractDigest"]
    del value["spec"]["authorityDomain"]
    value["spec"]["provenance"]["environment"] = "release-main-domain"
    value["spec"]["provenance"]["audience"] = "https://kube-api.re8ch.com/release-executor-main-domain"
    assert digest(canonical(value)) == migration["fromContractDigest"]


def test_suspended_release_rejects_replay_without_runtime_calls(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    instance.reconcile(value, operation, bundle_path, tmp_path / "state", verify_signature=False)
    store = executor.Store(tmp_path / "state", "demo")
    state = store.state()
    state["suspended"] = True
    store.accept(state)
    _, replay, _ = prepare(tmp_path, sequence=2, previous=state["appliedDigest"])
    runner.calls.clear()
    with pytest.raises(executor.Rejected, match="suspended"):
        instance.reconcile(value, replay, bundle_path, tmp_path / "state", verify_signature=False)
    assert runner.calls == []
    replay["action"] = "resume"
    instance.reconcile(value, replay, bundle_path, tmp_path / "state", verify_signature=False)
    assert store.state()["suspended"] is False
    assert runner.calls == []


@pytest.mark.parametrize("fault", [None, "blocked", "suspended", "healthyDigest", "releaseId", "bundleReference", "inventoryDigest", "namespace"])
def test_replay_resolves_only_target_bound_healthy_acceptance(tmp_path, fault):
    import yaml
    value, operation, bundle_path = prepare(tmp_path)
    instance = executor.Executor(runner=Runner(), now=lambda: NOW, sleeper=lambda _: None)
    instance.reconcile(value, operation, bundle_path, tmp_path / "state", verify_signature=False)
    state = executor.Store(tmp_path / "state", "demo").state()
    state["bundleReference"] = "oci://ghcr.io/re8ch/service-backend-release/demo"
    if fault in {"blocked", "suspended"}:
        state[fault] = True
    elif fault in {"healthyDigest", "inventoryDigest"}:
        state[fault] = "sha256:" + "f" * 64
    elif fault == "releaseId":
        state[fault] = "another-release"
    elif fault == "bundleReference":
        state[fault] = "oci://untrusted.example/demo"
    cm = {"metadata": {"name": "ssa-state-demo", "namespace": "other" if fault == "namespace" else "demo"},
          "data": {"accepted-state.json": json.dumps(state)}}
    cm_path = tmp_path / "cm.json"
    cm_path.write_text(json.dumps(cm))
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(yaml.safe_dump(value))
    if fault:
        with pytest.raises(ValueError):
            materialize.accepted_bundle(cm_path, contract_path)
    else:
        assert materialize.accepted_bundle(cm_path, contract_path) == {
            "reference": state["bundleReference"], "digest": state["appliedDigest"]}


def test_durable_state_is_a_server_side_apply_configmap(monkeypatch, tmp_path):
    state_dir = tmp_path / "state-root"
    (state_dir / "state").mkdir(parents=True)
    state = {"releaseId": "demo", "sequence": 1}
    result = {"releaseId": "demo", "outcome": "healthy"}
    (state_dir / "state/demo.json").write_text(json.dumps(state))
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result))
    output = tmp_path / "configmap.json"
    monkeypatch.setattr(materialize, "validate_schema", lambda *_: None)
    materialize.persist(state_dir, "demo", result_path, output)
    manifest = json.loads(output.read_text())
    assert manifest["apiVersion"] == "v1"
    assert manifest["kind"] == "ConfigMap"
    assert manifest["metadata"] == {"name": "ssa-state-demo", "namespace": "rook-ceph"}
    assert json.loads(manifest["data"]["accepted-state.json"]) == state
    assert json.loads(manifest["data"]["last-result.json"]) == result


def test_kubectl_commands_refresh_actions_oidc_token(monkeypatch):
    runner = Runner()
    instance = executor.Executor(runner=runner)
    monkeypatch.setattr(instance, "actions_kube_token", lambda: "fresh-token")
    instance.command(["kubectl", "get", "namespace", "demo"])
    assert runner.calls[0][0][:3] == ["kubectl", "--token=fresh-token", "get"]


def test_actions_oidc_refresh_is_cached_and_audience_bounded(monkeypatch):
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://pipelines.actions.githubusercontent.com/token?api-version=2.0")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "request-token")
    monkeypatch.setenv("EXECUTOR_AUDIENCE", "https://kube-api.re8ch.com/release-executor-ceph-infra")
    requests = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def open_token(request, timeout):
        requests.append(request)
        assert timeout == 30
        return Response(b'{"value":"fresh-token"}')

    monkeypatch.setattr(executor.urllib.request, "urlopen", open_token)
    instance = executor.Executor()
    assert instance.actions_kube_token() == "fresh-token"
    assert instance.actions_kube_token() == "fresh-token"
    assert len(requests) == 1
    monkeypatch.setenv("EXECUTOR_AUDIENCE", "https://attacker.example/release-executor-ceph-infra")
    rejected = executor.Executor()
    with pytest.raises(executor.Rejected, match="unexpected executor audience"):
        rejected.actions_kube_token()


def test_normal_apply_does_not_reclaim_conflicting_fields(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner(conflict=True)
    with pytest.raises(executor.Rejected, match="field manager conflict"):
        executor.Executor(runner=runner, now=lambda: NOW).reconcile(
            value, operation, bundle_path, tmp_path / "state", verify_signature=False)
    apply_calls = [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert len(apply_calls) == 1
    assert "--force-conflicts" not in apply_calls[0][0]


def test_force_conflicts_requires_action_and_contract_identity(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    identity = "apps/Deployment/demo/demo"
    value["spec"]["authorization"]["forceConflictObjects"] = [identity]
    archive, manifest = compile_bundle(value, rendered())
    bundle_path.write_bytes(archive)
    operation["contractDigest"] = digest(canonical(value))
    operation["bundle"]["digest"] = digest(archive)
    operation["bundle"]["payloadDigest"] = digest(archive)
    operation["inventoryDigest"] = manifest["inventoryDigest"]
    operation["action"] = "force-reconcile"
    operation["forceConflictObjects"] = [identity]
    runner = Runner(conflict=True)
    result = executor.Executor(runner=runner, now=lambda: NOW).reconcile(value, operation, bundle_path, tmp_path / "state", False)
    assert result["outcome"] == "healthy"
    assert all("--force-conflicts" in call[0] for call in runner.calls if call[0][:2] == ["kubectl", "apply"])


def test_explicit_health_check_reads_live_json_pointer():
    item = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "demo", "namespace": "demo"}}
    check = {"apiVersion": "v1", "kind": "ConfigMap", "name": "demo", "namespace": "demo",
             "jsonPointer": "/data/authority", "equals": "executor"}
    healthy = executor.Executor(runner=Runner(live={"data": {"authority": "executor"}}))
    unhealthy = executor.Executor(runner=Runner(live={"data": {"authority": "drift"}}))
    assert healthy.healthy(item, [check]) == (True, "contract health checks satisfied")
    assert unhealthy.healthy(item, [check]) == (False, "/data/authority differs from contract")


def test_completed_job_does_not_require_observed_generation():
    item = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "migrate", "namespace": "demo"}}
    live = {"metadata": {"generation": 1}, "spec": {"completions": 1}, "status": {"succeeded": 1}}
    assert executor.Executor(runner=Runner(live=live)).healthy(item) == (True, "job incomplete")


def test_rollback_pull_keeps_oci_scheme(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    archive = bundle_path.read_bytes()
    manifest, objects = executor.load_archive(bundle_path)
    previous = {
        "appliedDigest": operation["bundle"]["digest"],
        "bundleReference": operation["bundle"]["reference"],
        "payloadDigest": digest(archive),
        "inventory": manifest["inventory"],
        "objectRefs": [executor.object_ref(item) for item in objects],
    }
    runner = Runner(pulled_bundle=archive)
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    failed_operation = json.loads(json.dumps(operation))
    failed_operation["bundle"]["digest"] = "sha256:" + "f" * 64
    observed, _ = instance.rollback(previous, manifest, objects, value, failed_operation, "re8ch-release/demo",
                                    executor.Store(tmp_path / "state", "demo"), "drill")
    pull = next(call[0] for call in runner.calls if call[0][:3] == ["flux", "pull", "artifact"])
    assert pull[3].startswith("oci://registry.example/releases/demo@sha256:")
    assert observed == operation["bundle"]["digest"]
    applied = [json.loads(call[1]) for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert all(item["metadata"]["annotations"]["delivery.re8ch.com/artifact-digest"] == previous["appliedDigest"]
               and "delivery.re8ch.com/operation-id" not in item["metadata"]["annotations"]
               for item in applied)


def test_failed_rollback_remains_blocked_on_next_cloud_worker(tmp_path, monkeypatch):
    value, operation, bundle_path = prepare(tmp_path)
    state_root = tmp_path / "state"
    instance = executor.Executor(runner=Runner(), now=lambda: NOW, sleeper=lambda _: None)
    instance.reconcile(value, operation, bundle_path, state_root, verify_signature=False)
    previous = executor.Store(state_root, "demo").state()
    cm = {"metadata": {"name": "ssa-state-demo", "namespace": "demo", "resourceVersion": "71"},
          "data": {"accepted-state.json": json.dumps(previous)}}
    cm_path = tmp_path / "original-cm.json"
    cm_path.write_text(json.dumps(cm))
    _, replay, _ = prepare(tmp_path, sequence=2, previous=previous["appliedDigest"])
    monkeypatch.setattr(instance, "wait_healthy", lambda *args: (False, "not ready"))
    def failed_rollback(*args):
        raise executor.Rejected("previous artifact could not recover health")
    monkeypatch.setattr(instance, "rollback", failed_rollback)
    with pytest.raises(executor.Rejected, match="rollback failed"):
        instance.reconcile(value, replay, bundle_path, state_root, verify_signature=False)
    result_path = tmp_path / "result.json"
    result_path.write_text("")  # CLI failed before producing a successful result.
    patch_path = tmp_path / "patch.json"
    materialize.state_patch(cm_path, state_root, "demo", result_path, patch_path)
    patch = json.loads(patch_path.read_text())
    assert patch["metadata"] == {"resourceVersion": "71"}
    assert set(patch["data"]) == {"accepted-state.json"}
    assert json.loads(patch["data"]["accepted-state.json"])["blocked"] is True
    cm["data"].update(patch["data"])
    cm_path.write_text(json.dumps(cm))
    next_root = tmp_path / "fresh-cloud-worker"
    materialize.hydrate(cm_path, next_root, "demo")
    runner = Runner()
    with pytest.raises(executor.Rejected, match="release is blocked"):
        executor.Executor(runner=runner, now=lambda: NOW).reconcile(value, replay, bundle_path, next_root)
    assert runner.calls == []


def test_state_patch_does_not_write_unchanged_state_after_rejection(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    root = tmp_path / "state"
    result = executor.Executor(runner=Runner(), now=lambda: NOW).reconcile(
        value, operation, bundle_path, root, verify_signature=False)
    state = executor.Store(root, "demo").state()
    cm_path = tmp_path / "cm.json"
    cm_path.write_text(json.dumps({"metadata": {"name": "ssa-state-demo", "resourceVersion": "72"},
                                  "data": {"accepted-state.json": json.dumps(state), "last-result.json": json.dumps(result)}}))
    result_path = tmp_path / "result.json"
    result_path.write_text("")
    patch_path = tmp_path / "patch.json"
    patch_path.write_text("stale patch from earlier attempt")
    materialize.state_patch(cm_path, root, "demo", result_path, patch_path)
    assert not patch_path.exists()


def test_state_patch_persists_new_result_with_original_version(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    root = tmp_path / "state"
    instance = executor.Executor(runner=Runner(), now=lambda: NOW)
    first = instance.reconcile(value, operation, bundle_path, root, verify_signature=False)
    previous = executor.Store(root, "demo").state()
    cm_path = tmp_path / "cm.json"
    cm_path.write_text(json.dumps({"metadata": {"name": "ssa-state-demo", "resourceVersion": "73"},
                                  "data": {"accepted-state.json": json.dumps(previous), "last-result.json": json.dumps(first)}}))
    _, replay, _ = prepare(tmp_path, sequence=2, previous=previous["appliedDigest"])
    result = instance.reconcile(value, replay, bundle_path, root, verify_signature=False)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result))
    patch_path = tmp_path / "patch.json"
    materialize.state_patch(cm_path, root, "demo", result_path, patch_path)
    patch = json.loads(patch_path.read_text())
    assert patch["metadata"]["resourceVersion"] == "73"
    assert json.loads(patch["data"]["accepted-state.json"])["sequence"] == 2
    assert json.loads(patch["data"]["last-result.json"]) == result


def test_explicit_rollback_restores_only_runtime_stage_and_records_history(tmp_path):
    value, old_archive, old_manifest, old_path = staged_release(tmp_path, "old", "old", "stable")
    new_value, new_archive, new_manifest, new_path = staged_release(
        tmp_path, "new", "new", "stable", revision="c" * 40)
    state_root = tmp_path / "state"
    instance = executor.Executor(runner=Runner(), now=lambda: NOW, sleeper=lambda _: None)
    old_operation = operation_for(value, old_archive, old_manifest, 1)
    instance.reconcile(value, old_operation, old_path, state_root, verify_signature=False)
    new_operation = operation_for(new_value, new_archive, new_manifest, 2, old_operation["bundle"]["digest"])
    instance.reconcile(new_value, new_operation, new_path, state_root, verify_signature=False)

    rollback = operation_for(new_value, old_archive, old_manifest, 3, new_operation["bundle"]["digest"], "rollback")
    result = instance.reconcile(
        new_value, rollback, old_path, state_root, verify_signature=False, stages={"runtime"},
        current_bundle_path=new_path, rollback_stages={"runtime"})
    assert result["outcome"] == "rolled-back"
    assert result["rollbackDigest"] == old_operation["bundle"]["digest"]
    state = json.loads((state_root / "state/demo.json").read_text())
    assert state["appliedDigest"] == old_operation["bundle"]["digest"]
    assert [item["appliedDigest"] for item in state["history"]] == [new_operation["bundle"]["digest"]]


def test_explicit_rollback_rejects_changes_outside_runtime_stage(tmp_path):
    value, old_archive, old_manifest, old_path = staged_release(tmp_path, "old", "old", "old-config")
    new_value, new_archive, new_manifest, new_path = staged_release(
        tmp_path, "new", "new", "new-config", revision="c" * 40)
    state_root = tmp_path / "state"
    instance = executor.Executor(runner=Runner(), now=lambda: NOW, sleeper=lambda _: None)
    old_operation = operation_for(value, old_archive, old_manifest, 1)
    instance.reconcile(value, old_operation, old_path, state_root, verify_signature=False)
    new_operation = operation_for(new_value, new_archive, new_manifest, 2, old_operation["bundle"]["digest"])
    instance.reconcile(new_value, new_operation, new_path, state_root, verify_signature=False)
    rollback = operation_for(new_value, old_archive, old_manifest, 3, new_operation["bundle"]["digest"], "rollback")
    with pytest.raises(executor.Rejected, match="outside approved stages"):
        instance.reconcile(
            new_value, rollback, old_path, state_root, verify_signature=False, stages={"runtime"},
            current_bundle_path=new_path, rollback_stages={"runtime"})


def test_expired_out_of_order_and_reused_operation_are_rejected(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    operation["expiresAt"] = (NOW - dt.timedelta(seconds=1)).isoformat()
    with pytest.raises(executor.Rejected, match="stale"):
        executor.Executor(now=lambda: NOW).reconcile(value, operation, bundle_path, tmp_path / "state", False)
    value, operation, bundle_path = prepare(tmp_path, sequence=2)
    with pytest.raises(executor.Rejected, match="expected sequence 1"):
        executor.Executor(now=lambda: NOW).reconcile(value, operation, bundle_path, tmp_path / "state2", False)


@pytest.mark.parametrize("field", ["repository", "workflow", "ref", "environment", "audience"])
def test_operation_cannot_self_authorize_github_provenance(tmp_path, field):
    value, operation, bundle_path = prepare(tmp_path)
    operation["github"][field] = "attacker/value"
    if field == "workflow":
        operation["github"][field] = ".github/workflows/attacker.yml"
    elif field == "ref":
        operation["github"][field] = "refs/heads/attacker"
    elif field == "audience":
        operation["github"][field] = "https://attacker.example"
    with pytest.raises(executor.Rejected, match=f"GitHub {field}"):
        executor.Executor(now=lambda: NOW).reconcile(value, operation, bundle_path, tmp_path / "state", False)


def test_operation_cannot_self_authorize_signer(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    operation["bundle"]["signer"] = "https://github.com/attacker/workflow@refs/heads/main"
    with pytest.raises(executor.Rejected, match="bundle signer"):
        executor.Executor(now=lambda: NOW).reconcile(value, operation, bundle_path, tmp_path / "state", False)


def test_archive_path_or_extra_member_is_rejected(tmp_path):
    import gzip
    import io
    import tarfile
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("../objects.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"[]"))
    path = tmp_path / "bad.tar.gz"
    path.write_bytes(gzip.compress(raw.getvalue()))
    with pytest.raises(executor.Rejected, match="unexpected archive members"):
        executor.load_archive(path)


def prepared_cloud_replay(tmp_path):
    value, operation, path = prepare(tmp_path)
    value['spec']['authorityDomain'] = 'data-storage'
    value['spec']['provenance'].update({
        'workflow': '.github/workflows/main-domain-release.yml',
        'environment': 'release-executor-data-storage',
        'audience': 'https://kube-api.re8ch.com/release-executor-data-storage',
        'signer': 'https://github.com/re8ch/service-backend/.github/workflows/main-domain-release.yml@refs/heads/main',
    })
    archive, manifest = compile_bundle(value, rendered())
    path.write_bytes(archive)
    operation['contractDigest'] = digest(canonical(value))
    operation['bundle'].update({'reference': 'oci://ghcr.io/re8ch/service-backend-release/demo',
                               'digest': digest(archive), 'payloadDigest': digest(archive),
                               'signer': value['spec']['provenance']['signer']})
    operation['github'].update({k: value['spec']['provenance'][k] for k in ['repository', 'workflow', 'ref', 'environment', 'audience']})
    root = tmp_path / 'state'
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW, sleeper=lambda _: None)
    instance.reconcile(value, operation, path, root, verify_signature=False)
    replay = json.loads(json.dumps(operation))
    replay.update({'sequence': 2, 'operationId': str(uuid.uuid4()), 'previousApprovedDigest': operation['bundle']['digest']})
    replay['github'].update(executor.replay_provenance(value))
    runner.calls.clear()
    return value, replay, path, root, runner, instance


def test_replay_only_identity_reconciles_same_healthy_artifact(tmp_path):
    value, replay, path, root, runner, instance = prepared_cloud_replay(tmp_path)
    result = instance.reconcile(value, replay, path, root, verify_signature=False)
    assert result['outcome'] == 'healthy'
    assert executor.Store(root, 'demo').state()['sequence'] == 2
    assert any(call[0][:2] == ['kubectl', 'apply'] for call in runner.calls)


@pytest.mark.parametrize('fault', ['digest', 'reference', 'payloadDigest', 'inventoryDigest', 'contract',
                                  'force', 'rollback', 'reset', 'suspend', 'resume', 'force-reconcile',
                                  'unhealthy', 'blocked', 'suspended', 'missing-state',
                                  'repository', 'workflow', 'ref', 'environment', 'audience'])
def test_replay_only_identity_cannot_publish_or_change_authority(tmp_path, monkeypatch, fault):
    value, replay, path, root, runner, instance = prepared_cloud_replay(tmp_path)
    state = executor.Store(root, 'demo').state()
    if fault in {'digest', 'payloadDigest'}:
        replay['bundle'][fault] = 'sha256:' + 'f' * 64
    elif fault == 'reference':
        replay['bundle']['reference'] = 'oci://untrusted.example/demo'
    elif fault == 'inventoryDigest':
        replay[fault] = 'sha256:' + 'f' * 64
    elif fault == 'contract':
        value['spec']['lifecycle']['healthTimeoutSeconds'] += 1
        replay['contractDigest'] = digest(canonical(value))
        monkeypatch.setattr(executor, 'accepted_contract_migration', lambda *args: True)
    elif fault == 'force':
        replay['forceConflictObjects'] = ['apps/Deployment/demo/demo']
    elif fault in {'rollback', 'reset', 'suspend', 'resume', 'force-reconcile'}:
        replay['action'] = fault
    elif fault in {'unhealthy', 'blocked', 'suspended'}:
        state['healthyDigest' if fault == 'unhealthy' else fault] = 'sha256:' + 'f' * 64 if fault == 'unhealthy' else True
        executor.Store(root, 'demo').accept(state)
    elif fault == 'missing-state':
        executor.Store(root, 'demo').state_path.unlink()
        replay['sequence'] = 1
    else:
        replacements = {'repository': 'attacker/repo', 'workflow': '.github/workflows/other.yml',
                        'ref': 'refs/heads/other', 'environment': 'release-reconciler-other',
                        'audience': 'https://kube-api.re8ch.com/release-reconciler-other'}
        replay['github'][fault] = replacements[fault]
    with pytest.raises(executor.Rejected):
        instance.reconcile(value, replay, path, root, verify_signature=False)
    assert runner.calls == []


def test_replay_credentials_cannot_claim_publisher_operation(tmp_path, monkeypatch):
    value, replay, path, root, runner, instance = prepared_cloud_replay(tmp_path)
    monkeypatch.setenv('EXECUTOR_AUDIENCE', replay['github']['audience'])
    replay['github'].update({key: value['spec']['provenance'][key] for key in ['workflow', 'environment', 'audience']})
    with pytest.raises(executor.Rejected, match='actual OIDC'):
        instance.reconcile(value, replay, path, root, verify_signature=False)
    assert runner.calls == []


def test_materialized_replay_keeps_publisher_signature_and_actual_repository(tmp_path, monkeypatch):
    import yaml
    value, replay, path, root, runner, instance = prepared_cloud_replay(tmp_path)
    contract_path = tmp_path / 'contract.yaml'
    contract_path.write_text(yaml.safe_dump(value))
    metadata_path = tmp_path / 'metadata.json'
    metadata_path.write_text(json.dumps({'payloadDigest': replay['bundle']['payloadDigest'], 'inventoryDigest': replay['inventoryDigest']}))
    for key, value_env in {'GITHUB_REPOSITORY': 'attacker/repository', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123'}.items():
        monkeypatch.setenv(key, value_env)
    output = tmp_path / 'operation.json'
    materialize.request(contract_path, metadata_path, root, replay['bundle']['reference'], replay['bundle']['digest'], output, replay_only=True)
    operation = json.loads(output.read_text())
    assert operation['github']['repository'] == 'attacker/repository'
    assert operation['github']['environment'] == 'release-reconciler-data-storage'
    assert operation['bundle']['signer'] == value['spec']['provenance']['signer']
    assert operation['action'] == 'reconcile'
    with pytest.raises(ValueError, match='only accepted reconcile'):
        materialize.request(contract_path, metadata_path, root, replay['bundle']['reference'], replay['bundle']['digest'], output, action='rollback', replay_only=True)


def test_first_health_timeout_persists_failed_result_without_acceptance(tmp_path,monkeypatch):
    import itertools
    import uuid
    import yaml
    value,op,bundle=prepare(tmp_path)
    runner=Runner(live={'metadata':{'generation':2},'spec':{'replicas':1},'status':{'observedGeneration':2,'availableReplicas':0}})
    ticks=itertools.count(0,2);monkeypatch.setattr(executor.time,'monotonic',lambda:next(ticks))
    engine=executor.Executor(runner=runner,now=lambda:NOW,sleeper=lambda _:None)
    root=tmp_path/'root';result=engine.reconcile(value,op,bundle,root,False)
    assert result['outcome']=='failed' and result['health']['status']=='unhealthy'
    assert result['application']=={'status':'complete','attemptedInventory':['apps/Deployment/demo/demo'],'appliedInventory':['apps/Deployment/demo/demo']}
    assert result['observedDigest']==op['bundle']['digest']
    assert not (root/'state/demo.json').exists()
    cm=tmp_path/'cm.json';cm.write_text(json.dumps({'metadata':{'name':'ssa-state-demo','namespace':'demo','resourceVersion':'7'},'data':{}}))
    result_path=tmp_path/'result.json';result_path.write_text(json.dumps(result));patch=tmp_path/'patch.json'
    materialize.state_patch(cm,root,'demo',result_path,patch)
    payload=json.loads(patch.read_text());assert payload['metadata']=={'resourceVersion':'7'}
    assert set(payload['data'])=={'last-result.json'}
    cm.write_text(json.dumps({'metadata':{'name':'ssa-state-demo','namespace':'demo','resourceVersion':'8'},'data':payload['data']}))
    hydrated=tmp_path/'hydrated';materialize.hydrate(cm,hydrated,'demo')
    count=len(runner.calls)
    assert engine.reconcile(value,op,bundle,hydrated,False)==result
    assert len(runner.calls)==count
    # A new approved operation can retry; the failed result is not an accepted baseline.
    retry={**op,'operationId':str(uuid.uuid4()),'idempotencyKey':uuid.uuid4().hex,'nonce':uuid.uuid4().hex}
    runner.live=None
    assert engine.reconcile(value,retry,bundle,hydrated,False)['outcome']=='healthy'
    assert json.loads((hydrated/'state/demo.json').read_text())['sequence']==1


def test_partial_api_failure_records_acknowledged_and_uncertain_writes(tmp_path):
    value,archive,manifest,bundle=staged_release(tmp_path,'partial','v1','x')
    op=operation_for(value,archive,manifest,1)
    base=Runner()
    def runner(argv,**kwargs):
        if argv[:2]==['kubectl','apply'] and '--dry-run=server' not in argv and json.loads(kwargs['input'])['kind']=='ConfigMap':
            return subprocess.CompletedProcess(argv,1,'','request timed out; write outcome unknown')
        return base(argv,**kwargs)
    root=tmp_path/'root';result=executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,bundle,root,False)
    assert result['outcome']=='failed' and result['observedDigest'] is None
    assert result['application']['status']=='partial-or-unknown'
    assert result['application']['appliedInventory']==['apps/Deployment/demo/demo']
    assert result['application']['attemptedInventory']==manifest['inventory']
    assert not (root/'state/demo.json').exists()


def test_cli_prints_failure_result_and_returns_nonzero(tmp_path,monkeypatch,capsys):
    import itertools
    import uuid
    import yaml
    value,op,bundle=prepare(tmp_path)
    contract=tmp_path/'contract.yaml';contract.write_text(yaml.safe_dump(value))
    operation=tmp_path/'operation.json';operation.write_text(json.dumps(op))
    monkeypatch.setattr(sys,'argv',['ssa_executor.py','--contract',str(contract),'--operation',str(operation),'--bundle',str(bundle),'--state-dir',str(tmp_path/'root')])
    monkeypatch.setattr(executor.Executor,'reconcile',lambda *args,**kwargs:{'outcome':'failed'})
    assert executor.main()==1
    assert json.loads(capsys.readouterr().out)['outcome']=='failed'


@pytest.mark.parametrize('phase',['apply','health'])
def test_subprocess_timeout_is_durable_and_never_exposes_argv(tmp_path,phase):
    value,op,bundle=prepare(tmp_path)
    base=Runner()
    def runner(argv,**kwargs):
        if (phase=='apply' and argv[:2]==['kubectl','apply'] and '--dry-run=server' not in argv) or (phase=='health' and argv[:2]==['kubectl','get']):
            raise subprocess.TimeoutExpired(['kubectl','--token=fixture-secret'],120,output='sensitive output',stderr='sensitive stderr')
        return base(argv,**kwargs)
    root=tmp_path/'root'
    result=executor.Executor(runner=runner,now=lambda:NOW).reconcile(value,op,bundle,root,False)
    assert result['outcome']=='failed' and result['health']['status']=='unknown'
    assert result['observedDigest'] is None and result['application']['status']=='partial-or-unknown'
    assert result['application']['attemptedInventory']==['apps/Deployment/demo/demo']
    assert result['application']['appliedInventory']==([] if phase=='apply' else ['apps/Deployment/demo/demo'])
    assert not (root/'state/demo.json').exists()
    stored=(root/'results/demo'/f"{op['operationId']}.json").read_text()
    assert 'fixture-secret' not in stored and 'sensitive' not in stored and '--token' not in stored


@pytest.mark.parametrize('phase',['get','preflight'])
def test_timeout_retains_type_without_credential_argv_or_captured_streams(monkeypatch,phase):
    import traceback
    def runner(argv,**kwargs):
        assert '--token=fixture-credential' in argv
        raise subprocess.TimeoutExpired(argv,19,output='fixture-captured-output',stderr='fixture-captured-error')
    instance=executor.Executor(runner=runner)
    monkeypatch.setattr(instance,'actions_kube_token',lambda:'fixture-credential')
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        if phase=='get':instance.command(['kubectl','get','deployment','demo'])
        else:instance.apply([{'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'demo','namespace':'demo'}}],'fixture-manager',dry_run=True)
    error=caught.value
    assert error.cmd==['kubectl'] and error.timeout==19
    assert error.output is None and error.stderr is None and error.__suppress_context__
    rendered=''.join(traceback.format_exception(type(error),error,error.__traceback__))
    assert 'fixture-credential' not in rendered
    assert 'fixture-captured-output' not in rendered and 'fixture-captured-error' not in rendered


def test_same_digest_reconcile_preserves_object_payload_and_distinct_result_audit(tmp_path):
    value, first, bundle = prepare(tmp_path)
    runner = Runner()
    instance = executor.Executor(runner=runner, now=lambda: NOW)
    root = tmp_path / "state"
    one = instance.reconcile(value, first, bundle, root, verify_signature=False)
    first_payloads = [call[1] for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    runner.calls.clear()
    _, second, _ = prepare(tmp_path, sequence=2, previous=first["bundle"]["digest"])
    two = instance.reconcile(value, second, bundle, root, verify_signature=False)
    second_payloads = [call[1] for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert first_payloads == second_payloads
    assert one["operationId"] != two["operationId"]
    store = executor.Store(root, "demo")
    assert store.result(first["operationId"])["operationId"] == first["operationId"]
    assert store.result(second["operationId"])["operationId"] == second["operationId"]
    assert store.state()["lastOperationId"] == second["operationId"]


def test_stable_object_audit_removes_legacy_attempt_id_without_mutating_bundle(tmp_path):
    _, op, bundle = prepare(tmp_path)
    _, objects = executor.load_archive(bundle)
    annotations = {"delivery.re8ch.com/operation-id": "old-attempt", "example.com/owner": "kept"}
    objects[0]["metadata"]["annotations"] = annotations.copy()
    result = executor.Executor.with_audit(objects, op)
    assert objects[0]["metadata"]["annotations"] == annotations
    assert result[0]["metadata"]["annotations"] == {
        "example.com/owner": "kept", "delivery.re8ch.com/release-id": op["releaseId"],
        "delivery.re8ch.com/artifact-digest": op["bundle"]["digest"]}
    next_op = json.loads(json.dumps(op)); next_op["bundle"]["digest"] = "sha256:" + "f" * 64
    updated = executor.Executor.with_audit(objects, next_op)
    assert updated[0]["metadata"]["annotations"]["delivery.re8ch.com/artifact-digest"] != result[0]["metadata"]["annotations"]["delivery.re8ch.com/artifact-digest"]
