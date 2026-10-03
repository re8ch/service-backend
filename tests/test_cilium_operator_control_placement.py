from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "operations/network/manifests/cilium/cilium-operator-a1-b1-ha.yaml"
VALUES = ROOT / "operations/network/manifests/cilium/advanced-fabric-native-routing-values.yaml"


def selected_hosts(affinity):
    expression = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"][0]
    assert expression["key"] == "kubernetes.io/hostname"
    assert expression["operator"] == "In"
    return expression["values"]


def test_operator_stays_replicated_on_control_planes_without_r640():
    deployment, pdb = yaml.safe_load_all(MANIFEST.read_text())
    values = yaml.safe_load(VALUES.read_text())["operator"]
    assert deployment["spec"]["replicas"] == values["replicas"] == 2
    assert selected_hosts(deployment["spec"]["template"]["spec"]["affinity"]) == [
        "restricted-node-1eb0570f74", "restricted-node-abb3f27a69"
    ]
    assert selected_hosts(values["affinity"]) == ["restricted-node-1eb0570f74", "restricted-node-abb3f27a69"]
    assert pdb["spec"]["minAvailable"] == 1
    assert deployment["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]["topologyKey"] == "kubernetes.io/hostname"
