from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_observable_postgrest_uses_owned_core_service_plane():
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
        "key": "scheduling.re8ch.com/service-plane",
        "operator": "In",
        "values": ["core"],
    }
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 1
    assert "requiredDuringSchedulingIgnoredDuringExecution" in pod_spec["affinity"][
        "podAntiAffinity"
    ]
