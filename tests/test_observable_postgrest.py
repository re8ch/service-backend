from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_observable_postgrest_is_owned_by_root_gitops():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/observable/postgrest-ha.yaml" in root["resources"]


def test_observable_postgrest_stays_on_database_route_verified_nodes():
    resources = [
        item
        for item in yaml.safe_load_all(
            (ROOT / "clusters/re8ch/components/observable/postgrest-ha.yaml").read_text()
        )
        if item
    ]
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    expression = pod_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
    }
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 1
    assert "requiredDuringSchedulingIgnoredDuringExecution" in pod_spec["affinity"][
        "podAntiAffinity"
    ]
