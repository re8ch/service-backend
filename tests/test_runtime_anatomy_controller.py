import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "clusters/re8ch/charts/runtime-anatomy/files/controller.py"


def load_controller():
    spec = importlib.util.spec_from_file_location("runtime_anatomy_controller", CONTROLLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_manifests_a_csi_snapshot_materialization(monkeypatch):
    controller = load_controller()
    commit = controller.adapter_program("commit", "ra-test")
    assert 'root="/source"' in commit
    assert "os.mkdir" not in commit
    subprocess.run(["python3", "-c", commit], check=True, capture_output=True, text=True)
    calls = []
    monkeypatch.setattr(controller, "request", lambda path, method="GET", body=None, **kwargs: calls.append((path, method, body)) or (None if method == "GET" else body))
    source = {"spec": {"accessModes": ["ReadWriteMany"], "storageClassName": "cephfs-re8ch", "resources": {"requests": {"storage": "1Gi"}}}}
    pvc = controller.ensure_snapshot_pvc("runtime-anatomy-canary", "materialized", source, "ra-test")
    assert pvc["spec"]["dataSource"] == {"apiGroup": "snapshot.storage.k8s.io", "kind": "VolumeSnapshot", "name": "ra-test"}
    assert pvc["metadata"]["labels"]["storage.re8ch.com/replicated-approved"] == "true"


def test_manifest_entries_are_keyed_for_deterministic_diff():
    controller = load_controller()
    commit = {"status": {"manifest": {"entries": [
        {"path": "b.txt", "sha256": "b"},
        {"path": "a.txt", "sha256": "a"},
    ]}}}
    assert controller.manifest_entries(commit) == {
        "a.txt": {"path": "a.txt", "sha256": "a"},
        "b.txt": {"path": "b.txt", "sha256": "b"},
    }


def test_job_security_and_canary_labels_are_enforced():
    controller = load_controller()
    job = controller.job_body("runtime-anatomy-canary", "job", "source", "commit", "ra-test")
    assert job["metadata"]["labels"][controller.CANARY_LABEL] == "true"
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False


def test_quantity_comparison_blocks_smaller_restore_targets():
    controller = load_controller()
    assert controller.quantity_bytes("1Mi") < controller.quantity_bytes("1Gi")
    assert controller.quantity_bytes("2Gi") == 2 * 1024 * 1024 * 1024
