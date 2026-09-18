from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = ROOT / "platform/scheduling-fabric/governance"


def docs(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_controller_releases_are_pinned_and_initially_non_disruptive():
    bootstrap = docs(GOVERNANCE / "controllers.yaml")
    releases = {
        item["metadata"]["name"]: item
        for item in docs(GOVERNANCE / "controllers/releases.yaml")
        if item["kind"] == "HelmRelease"
    }
    assert releases["descheduler"]["spec"]["chart"]["spec"]["version"] == "0.36.0"
    assert releases["descheduler"]["spec"]["values"]["cmdOptions"]["dry-run"] is True
    assert releases["descheduler"]["spec"]["values"]["deschedulerPolicy"]["maxNoOfPodsToEvictTotal"] == 5
    assert releases["kyverno"]["spec"]["chart"]["spec"]["version"] == "3.9.1"
    assert releases["kueue"]["spec"]["chart"]["spec"] == {
        "chart": "charts/kueue",
        "reconcileStrategy": "Revision",
        "sourceRef": {"kind": "GitRepository", "name": "kueue-official"},
    }

    kueue_source = next(
        item
        for item in bootstrap
        if item["kind"] == "GitRepository" and item["metadata"]["name"] == "kueue-official"
    )
    assert kueue_source["kind"] == "GitRepository"
    assert kueue_source["spec"]["ref"]["commit"] == "8e60d76a91bb9a45754218e569de1088f862ac75"

    child_kustomizations = {
        item["metadata"]["name"]: item
        for item in bootstrap
        if item["kind"] == "Kustomization"
    }
    assert child_kustomizations["scheduling-governance-controllers"]["spec"]["wait"] is True
    assert child_kustomizations["scheduling-governance-policies"]["spec"]["dependsOn"] == [
        {"name": "scheduling-governance-controllers"}
    ]


def test_policies_start_in_audit_and_kueue_is_explicit_opt_in():
    policies = docs(GOVERNANCE / "policies/kyverno.yaml")
    assert all(item["spec"]["validationFailureAction"] == "Audit" for item in policies)
    assert "node-role.kubernetes.io/control-plane" in (GOVERNANCE / "policies/kyverno.yaml").read_text()

    queues = docs(GOVERNANCE / "policies/kueue.yaml")
    local_queues = [item for item in queues if item["kind"] == "LocalQueue"]
    assert {(item["metadata"]["namespace"], item["metadata"]["name"]) for item in local_queues} == {
        ("tenant-qwen-runtime", "tenant-batch"),
        ("artchais-default", "tenant-batch"),
    }


def test_control_plane_reservations_are_declared_without_noexecute():
    configs = {
        name: yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / name).read_text())
        for name in ("r640-k3s-server-config.yaml", "a1-wsl2-k3s-server-config.yaml", "b1-wsl2-k3s-server-config.yaml")
    }
    for config in configs.values():
        args = config["kubelet-arg"]
        assert "max-pods=256" in args
        assert any(value.startswith("kube-reserved=") for value in args)
        assert any(value.startswith("system-reserved=") for value in args)
        assert "enforce-node-allocatable=pods" in args
        assert not any("NoExecute" in value for value in args)


def test_dynamic_scheduler_uses_production_metrics_and_hard_spread():
    scheduler = (ROOT / "platform/scheduling-fabric/resources/scheduler.yaml").read_text()
    controller = (ROOT / "platform/scheduling-fabric/resources/controller.yaml").read_text()
    expected = "vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus"
    assert expected in scheduler
    assert expected in controller
    assert "whenUnsatisfiable: DoNotSchedule" in scheduler


def test_root_includes_governance_but_no_node_taint_object():
    kustomization = yaml.safe_load((ROOT / "platform/scheduling-fabric/kustomization.yaml").read_text())
    assert "governance/controllers.yaml" in kustomization["resources"]
    rendered_text = "\n".join(path.read_text() for path in (ROOT / "platform/scheduling-fabric").rglob("*.yaml"))
    assert "effect: NoExecute" not in rendered_text
