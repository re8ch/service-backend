import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
PATH = ROOT / "operations/scripts/control-plane-workload-inventory.py"
SPEC = importlib.util.spec_from_file_location("control_plane_inventory", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def resource(kind, namespace, name, *, owner=None, template=None, node=None, phase=None):
    item = {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": {"namespace": namespace, "name": name},
        "spec": {},
    }
    if owner:
        item["metadata"]["ownerReferences"] = [
            {"kind": owner[0], "name": owner[1], "controller": True}
        ]
    if template is not None:
        item["spec"]["template"] = template
    if node:
        item["spec"]["nodeName"] = node
    if phase:
        item["status"] = {"phase": phase}
    return item


def test_inventory_resolves_deployment_and_distinguishes_managed_from_unmanaged():
    managed_template = {
        "metadata": {"labels": {}},
        "spec": {"nodeSelector": {"kubernetes.io/hostname": "r640"}},
    }
    unmanaged_template = {"metadata": {"labels": {}}, "spec": {}}
    items = [
        resource("Deployment", "apps", "managed", template=managed_template),
        resource("ReplicaSet", "apps", "managed-rs", owner=("Deployment", "managed")),
        resource(
            "Pod",
            "apps",
            "managed-pod",
            owner=("ReplicaSet", "managed-rs"),
            node="r640",
            phase="Running",
        ),
        resource("Deployment", "apps", "unmanaged", template=unmanaged_template),
        resource("ReplicaSet", "apps", "unmanaged-rs", owner=("Deployment", "unmanaged")),
        resource(
            "Pod",
            "apps",
            "unmanaged-pod",
            owner=("ReplicaSet", "unmanaged-rs"),
            node="r640",
            phase="Running",
        ),
    ]
    records = MODULE.inventory(items, {"r640"})
    by_pod = {record["pod"]: record for record in records}
    assert by_pod["managed-pod"]["ownerKind"] == "Deployment"
    assert by_pod["managed-pod"]["ownerName"] == "managed"
    assert by_pod["managed-pod"]["classification"] == "managed-conflict"
    assert by_pod["unmanaged-pod"]["classification"] == "unmanaged"


def test_approved_template_and_daemonset_are_not_hard_taint_conflicts():
    approved_template = {
        "metadata": {
            "labels": {"scheduling.re8ch.com/control-plane-allowed": "true"}
        },
        "spec": {},
    }
    items = [
        resource("Deployment", "apps", "approved", template=approved_template),
        resource("ReplicaSet", "apps", "approved-rs", owner=("Deployment", "approved")),
        resource(
            "Pod",
            "apps",
            "approved-pod",
            owner=("ReplicaSet", "approved-rs"),
            node="r640",
            phase="Running",
        ),
        resource("DaemonSet", "apps", "node-agent", template={"metadata": {}, "spec": {}}),
        resource(
            "Pod",
            "apps",
            "node-agent-r640",
            owner=("DaemonSet", "node-agent"),
            node="r640",
            phase="Running",
        ),
    ]
    classifications = {
        record["pod"]: record["classification"]
        for record in MODULE.inventory(items, {"r640"})
    }
    assert classifications == {
        "approved-pod": "approved",
        "node-agent-r640": "daemon",
    }
