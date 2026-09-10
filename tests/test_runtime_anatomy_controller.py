import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "clusters/re8ch/charts/runtime-anatomy/files/controller.py"


def load_controller():
    spec = importlib.util.spec_from_file_location("runtime_anatomy_controller", CONTROLLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_uses_cephfs_native_snapshot_and_new_target_mount():
    controller = load_controller()
    commit = controller.adapter_program("commit", "ra-test")
    restore = controller.adapter_program("restore", "ra-test")
    assert 'os.path.join(root,".snap",snap)' in commit
    assert 'os.mkdir(snapdir)' in commit
    assert 'os.path.join("/source",".snap"' in restore
    assert 'target="/target"' in restore


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
