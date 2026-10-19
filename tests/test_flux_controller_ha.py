from pathlib import Path
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / "clusters/re8ch"
CORE_CONTROLLERS = {
    "helm-controller",
    "notification-controller",
    "kustomize-controller",
}
DISRUPTION_BUDGETED = CORE_CONTROLLERS
FAILOVER_NODES = ["overseas-kl", "restricted-node-6dc3f140f9"]


def rendered_documents():
    command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    output = subprocess.check_output([*command, str(CLUSTER)], text=True)
    return list(yaml.safe_load_all(output))


def test_flux_core_controllers_use_kl_r640_active_standby():
    deployments = {
        item["metadata"]["name"]: item
        for item in rendered_documents()
        if item and item["kind"] == "Deployment" and item["metadata"].get("namespace") == "flux-system"
    }
    for name in CORE_CONTROLLERS:
        deployment = deployments[name]
        assert deployment["spec"]["replicas"] == 2
        assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
        pod = deployment["spec"]["template"]["spec"]
        expression = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression["key"] == "kubernetes.io/hostname"
        assert expression["values"] == FAILOVER_NODES
        assert pod["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
        preferred = pod["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"]
        assert preferred[0]["weight"] == 100
        assert preferred[0]["preference"]["matchExpressions"][0]["values"] == ["overseas-kl"]
        assert pod.get("dnsPolicy", "ClusterFirst") == "ClusterFirst"
        env = {item["name"]: item for item in pod["containers"][0]["env"]}
        assert env["KUBERNETES_SERVICE_HOST"]["value"] == "192.0.2.116"
        assert env["KUBERNETES_SERVICE_PORT"]["value"] == "6443"
        assert env["KUBERNETES_SERVICE_PORT_HTTPS"]["value"] == "6443"
        assert "dnsConfig" not in pod
        assert "--enable-leader-election" in pod["containers"][0]["args"]
        if name == "helm-controller":
            assert "--concurrent=1" in pod["containers"][0]["args"]


def test_kustomize_controller_colocates_with_source_and_uses_control_plane_ha_proxy():
    deployment = next(
        item for item in rendered_documents()
        if item and item["kind"] == "Deployment"
        and item["metadata"].get("namespace") == "flux-system"
        and item["metadata"]["name"] == "kustomize-controller"
    )
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    pod = deployment["spec"]["template"]["spec"]
    expression = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression["values"] == FAILOVER_NODES
    env = {item["name"]: item["value"] for item in pod["containers"][0]["env"] if "value" in item}
    assert env["KUBERNETES_SERVICE_HOST"] == "192.0.2.116"
    assert env["KUBERNETES_SERVICE_PORT"] == "6443"
    assert "--concurrent=1" in pod["containers"][0]["args"]
    assert {item.get("key") for item in pod["tolerations"]} == {
        "dedicated",
        "scheduling.re8ch.com/capacity-window",
    }


def test_source_controller_uses_kl_r640_scheduler_failover():
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
    assert expression["values"] == FAILOVER_NODES
    assert pod["nodeSelector"] == {"kubernetes.io/os": "linux"}
    preferred = pod["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"]
    assert preferred[0]["weight"] == 100
    assert preferred[0]["preference"]["matchExpressions"][0]["values"] == ["overseas-kl"]
    assert pod["hostNetwork"] is False
    assert pod["dnsPolicy"] == "ClusterFirst"
    assert "dnsConfig" not in pod
    manager = pod["containers"][0]
    env = {item["name"]: item.get("value") for item in manager["env"]}
    assert env["KUBERNETES_SERVICE_HOST"] == "192.0.2.116"
    assert env["KUBERNETES_SERVICE_PORT"] == "6443"
    assert "--storage-adv-addr=source-controller.$(RUNTIME_NAMESPACE).svc.cluster.local." in manager["args"]
    assert "--enable-leader-election" in manager["args"]
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
    assert {item["metadata"]["name"] for item in budgets} == DISRUPTION_BUDGETED
    for item in budgets:
        assert item["kind"] == "PodDisruptionBudget"
        assert item["spec"]["minAvailable"] == 1
        assert item["spec"]["selector"]["matchLabels"]["app"] == item["metadata"]["name"]


def test_root_kustomization_applies_ha_overlay_without_editing_generated_flux():
    root = yaml.safe_load((CLUSTER / "kustomization.yaml").read_text())
    assert "flux-system/controller-ha.yaml" in root["resources"]
    assert any(
        patch.get("target", {}).get("name")
        == "(helm-controller|notification-controller)"
        for patch in root["patches"]
    )


def test_root_kustomization_allows_slow_artifact_delivery():
    sync = list(yaml.safe_load_all((CLUSTER / "flux-system/gotk-sync.yaml").read_text()))
    root = next(item for item in sync if item["kind"] == "Kustomization")
    assert root["spec"]["timeout"] == "20m"
