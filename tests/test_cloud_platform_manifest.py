from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def resources():
    return list(yaml.safe_load_all((ROOT / "platform/cloud-platform/runtime.yaml").read_text()))


def test_cloud_platform_can_schedule_on_any_control_plane_node():
    deployment = next(item for item in resources() if item["kind"] == "Deployment")
    affinity = deployment["spec"]["template"]["spec"]["affinity"]
    expressions = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"]

    assert expressions == [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"}]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in affinity["podAntiAffinity"]
    assert affinity["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"][0]["weight"] == 100


def test_cloud_platform_keeps_one_replica_during_voluntary_disruption():
    pdb = next(item for item in resources() if item["kind"] == "PodDisruptionBudget")

    assert pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"]["matchLabels"] == {"app": "re8ch-cloud-platform"}
