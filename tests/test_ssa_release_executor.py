import datetime as dt
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/releases"))
from unified_bundle import canonical, compile_bundle, digest

SPEC = importlib.util.spec_from_file_location("ssa_executor", ROOT / "operations/releases/ssa_executor.py")
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)

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


def test_authorized_apply_reclaims_conflicting_fields(tmp_path):
    value, operation, bundle_path = prepare(tmp_path)
    runner = Runner(conflict=True)
    result = executor.Executor(runner=runner, now=lambda: NOW).reconcile(
        value, operation, bundle_path, tmp_path / "state", verify_signature=False)
    apply_calls = [call for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert result["outcome"] == "healthy"
    assert len(apply_calls) == 2
    assert all("--force-conflicts" in call[0] for call in apply_calls)


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
    observed, _ = instance.rollback(previous, manifest, objects, value, operation, "re8ch-release/demo",
                                    executor.Store(tmp_path / "state", "demo"), "drill")
    pull = next(call[0] for call in runner.calls if call[0][:3] == ["flux", "pull", "artifact"])
    assert pull[3].startswith("oci://registry.example/releases/demo@sha256:")
    assert observed == operation["bundle"]["digest"]
    applied = [json.loads(call[1]) for call in runner.calls if call[0][:2] == ["kubectl", "apply"]]
    assert all(item["metadata"]["annotations"]["delivery.re8ch.com/operation-id"] == operation["operationId"]
               for item in applied)


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
