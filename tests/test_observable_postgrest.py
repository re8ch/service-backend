from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_observable_postgrest_is_retired_but_namespace_is_retained():
    domain = yaml.safe_load((ROOT / "clusters/re8ch/domains/data-storage/source/kustomization.yaml").read_text())
    assert "../../../components/observable/postgrest-ha.yaml" not in domain["resources"]
    assert "retained-observable.yaml" in domain["resources"]
    retained = yaml.safe_load((ROOT / "clusters/re8ch/domains/data-storage/source/retained-observable.yaml").read_text())
    assert retained["kind"] == "Namespace"
    assert retained["metadata"]["name"] == "observable"
    assert retained["metadata"]["labels"]["delivery.re8ch.com/retention"] == "retained-data"
    compiled = (ROOT / "clusters/re8ch/domains/data-storage/resources.yaml").read_text()
    assert "name: observable-postgrest\n" not in compiled


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
