from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_cert_manager_webhook_is_ha_on_owned_service_plane():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert (
        "components/regional-routing/cert-manager-bootstrap.yaml"
        in root["resources"]
    )

    kustomization = yaml.safe_load(
        (
            ROOT / "clusters/re8ch/components/regional-routing/kustomization.yaml"
        ).read_text()
    )
    assert "cert-manager-bootstrap.yaml" in kustomization["resources"]

    resources = [
        item
        for item in yaml.safe_load_all(
            (
                ROOT
                / "clusters/re8ch/components/regional-routing/cert-manager-bootstrap.yaml"
            ).read_text()
        )
        if item
    ]
    chart = next(item for item in resources if item["kind"] == "HelmChart")
    values = yaml.safe_load(chart["spec"]["valuesContent"])
    webhook = values["webhook"]

    assert chart["spec"]["version"] == "v1.21.1"
    assert chart["spec"]["backOffLimit"] == 20
    assert webhook["replicaCount"] == 2
    assert webhook["timeoutSeconds"] == 10
    assert webhook["hostNetwork"] is True
    assert webhook["securePort"] == 10260
    assert webhook["podDisruptionBudget"] == {
        "enabled": True,
        "minAvailable": 1,
    }
    expression = webhook["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression == {
        "key": "scheduling.re8ch.com/service-plane",
        "operator": "In",
        "values": ["core"],
    }
    anti_affinity = webhook["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]
    assert anti_affinity["topologyKey"] == "kubernetes.io/hostname"
