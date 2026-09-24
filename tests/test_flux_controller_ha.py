from pathlib import Path
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / "clusters/re8ch"
REPLICATED = {
    "helm-controller",
    "kustomize-controller",
    "notification-controller",
}
CONTROL_PLANES = {"restricted-node-6dc3f140f9", "restricted-node-1eb0570f74", "restricted-node-296f36f2fb"}


def rendered_documents():
    command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    output = subprocess.check_output([*command, str(CLUSTER)], text=True)
    return list(yaml.safe_load_all(output))


def test_flux_controllers_use_three_control_planes_and_stable_dns():
    deployments = {
        item["metadata"]["name"]: item
        for item in rendered_documents()
        if item and item["kind"] == "Deployment" and item["metadata"].get("namespace") == "flux-system"
    }
    for name in REPLICATED:
        deployment = deployments[name]
        assert deployment["spec"]["replicas"] == 3
        assert deployment["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 1}
        pod = deployment["spec"]["template"]["spec"]
        expression = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression["key"] == "kubernetes.io/hostname"
        assert set(expression["values"]) == CONTROL_PLANES
        assert pod["dnsPolicy"] == "ClusterFirst"
        assert "dnsConfig" not in pod
        assert {
            "key": "node-role.kubernetes.io/control-plane",
            "operator": "Exists",
            "effect": "NoSchedule",
        } in pod["tolerations"]
        anti_affinity = pod["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
        assert anti_affinity["topologyKey"] == "kubernetes.io/hostname"
        assert anti_affinity["matchLabelKeys"] == ["app"]
        assert "--enable-leader-election" in pod["containers"][0]["args"]
        if name in {"helm-controller", "kustomize-controller"}:
            assert "--concurrent=10" in pod["containers"][0]["args"]


def test_source_controller_is_single_active_with_three_node_failover():
    documents = rendered_documents()
    deployment = next(
        item for item in documents
        if item and item["kind"] == "Deployment"
        and item["metadata"].get("namespace") == "flux-system"
        and item["metadata"]["name"] == "source-controller"
    )
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    pod = deployment["spec"]["template"]["spec"]
    expression = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert set(expression["values"]) == CONTROL_PLANES
    assert pod["dnsPolicy"] == "ClusterFirst"
    assert "dnsConfig" not in pod
    manager = pod["containers"][0]
    assert "--storage-adv-addr=source-controller.$(RUNTIME_NAMESPACE).svc.cluster.local." in manager["args"]
    assert all(item["name"] != "POD_IP" for item in manager.get("env", []))
    service = next(
        item for item in documents
        if item and item["kind"] == "Service"
        and item["metadata"].get("namespace") == "flux-system"
        and item["metadata"]["name"] == "source-controller"
    )
    assert "clusterIP" not in service["spec"]
    assert "clusterIPs" not in service["spec"]


def test_replicated_controllers_have_quorum_disruption_budgets():
    budgets = list(yaml.safe_load_all((CLUSTER / "flux-system/controller-ha.yaml").read_text()))
    assert {item["metadata"]["name"] for item in budgets} == REPLICATED
    for item in budgets:
        assert item["kind"] == "PodDisruptionBudget"
        assert item["spec"]["minAvailable"] == 2
        assert item["spec"]["selector"]["matchLabels"]["app"] == item["metadata"]["name"]


def test_root_kustomization_applies_ha_overlay_without_editing_generated_flux():
    root = yaml.safe_load((CLUSTER / "kustomization.yaml").read_text())
    assert "flux-system/controller-ha.yaml" in root["resources"]
    assert any(
        patch.get("target", {}).get("name")
        == "(helm-controller|kustomize-controller|notification-controller)"
        for patch in root["patches"]
    )
