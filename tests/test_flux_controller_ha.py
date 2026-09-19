from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / "clusters/re8ch"


def test_helm_controller_has_two_failure_domain_separated_replicas():
    patch = yaml.safe_load(
        (CLUSTER / "flux-system/helm-controller-ha.patch.yaml").read_text()
    )
    spec = patch["spec"]
    assert spec["replicas"] == 2
    assert spec["strategy"]["rollingUpdate"] == {
        "maxSurge": 1,
        "maxUnavailable": 0,
    }
    pod_spec = spec["template"]["spec"]
    terms = pod_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"]
    expression = terms[0]["matchExpressions"][0]
    assert expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-6dc3f140f9", "domain-ex", "vm-20-12-ubuntu"],
    }
    anti_affinity = pod_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]
    assert anti_affinity["topologyKey"] == "kubernetes.io/hostname"
    assert anti_affinity["labelSelector"]["matchLabels"] == {
        "app": "helm-controller"
    }


def test_helm_controller_ha_has_a_minimum_availability_budget():
    budget = yaml.safe_load((CLUSTER / "flux-system/controller-ha.yaml").read_text())
    assert budget["kind"] == "PodDisruptionBudget"
    assert budget["spec"]["minAvailable"] == 1
    assert budget["spec"]["selector"]["matchLabels"] == {
        "app": "helm-controller"
    }


def test_root_kustomization_applies_ha_overlay_without_editing_generated_flux():
    root = yaml.safe_load((CLUSTER / "kustomization.yaml").read_text())
    assert "flux-system/controller-ha.yaml" in root["resources"]
    assert {
        "path": "flux-system/helm-controller-ha.patch.yaml",
        "target": {
            "group": "apps",
            "version": "v1",
            "kind": "Deployment",
            "name": "helm-controller",
            "namespace": "flux-system",
        },
    } in root["patches"]
