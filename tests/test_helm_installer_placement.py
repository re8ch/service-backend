from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_k3s_helm_installers_are_pinned_to_local_egress_nodes():
    directory = ROOT / "platform/scheduling-fabric/governance/policies"
    kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text())
    assert "helm-installer-placement.yaml" in kustomization["resources"]

    policy = yaml.safe_load((directory / "helm-installer-placement.yaml").read_text())
    assert policy["metadata"]["name"] == "place-k3s-helm-installers-on-local-egress"
    rule = policy["spec"]["rules"][0]
    matched = rule["match"]["any"][0]["resources"]
    assert matched["kinds"] == ["Job"]
    assert matched["namespaces"] == ["kube-system"]
    assert matched["names"] == ["helm-install-*"]
    assert matched["selector"]["matchExpressions"] == [
        {
            "key": "helmcharts.helm.cattle.io/chart",
            "operator": "Exists",
        }
    ]
    expression = rule["mutate"]["patchStrategicMerge"]["spec"]["template"][
        "spec"
    ]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
    }
