import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/scripts/rollout-vlagent-ready-nodes.py"
SPEC = importlib.util.spec_from_file_location("vlagent_rollout", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def node(name, ready):
    return {
        "metadata": {"name": name},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def pod(name, node_name, args, deleting=False):
    metadata = {
        "name": name,
        "ownerReferences": [{"kind": "DaemonSet", "name": MODULE.DAEMONSET}],
    }
    if deleting:
        metadata["deletionTimestamp"] = "2026-09-18T00:00:00Z"
    return {
        "metadata": metadata,
        "spec": {"nodeName": node_name, "containers": [{"name": MODULE.CONTAINER, "args": args}]},
    }


def test_selects_only_outdated_non_deleting_pods_on_ready_nodes():
    daemonset = {
        "spec": {"template": {"spec": {"containers": [{"name": MODULE.CONTAINER, "args": ["new"]}]}}}
    }
    pods = {
        "items": [
            pod("replace", "ready", ["old"]),
            pod("current", "ready", ["new"]),
            pod("offline", "offline", ["old"]),
            pod("deleting", "ready", ["old"], deleting=True),
        ]
    }
    nodes = {"items": [node("ready", True), node("offline", False)]}

    selected = MODULE.outdated_ready_node_pods(daemonset, pods, nodes)
    assert [item["metadata"]["name"] for item in selected] == ["replace"]


def test_script_never_uses_force_deletion():
    text = SCRIPT.read_text()
    assert '"--force"' not in text
    assert '"--grace-period=0"' not in text
    assert 'default=1' in text
