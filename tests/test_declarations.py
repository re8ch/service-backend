from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def definitions():
    for path in (ROOT / "plugins").glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        yield from data.get("items", [data])


def test_plugin_names_and_prefixes_are_unique():
    docs = list(definitions())
    names = [doc["metadata"]["name"] for doc in docs]
    prefixes = [doc["spec"]["publication"]["toolPrefix"] for doc in docs]
    assert len(names) == len(set(names))
    assert len(prefixes) == len(set(prefixes))


def test_public_edge_candidate_algorithm_is_denied():
    public_edge = next(doc for doc in definitions() if doc["metadata"]["name"] == "public-edge")
    assert "candidateSelection" in public_edge["spec"]["policy"]["deniedFields"]


def test_every_plugin_has_independent_identity():
    identities = [doc["spec"]["identityRef"] for doc in definitions()]
    assert len(identities) == len(set(identities))


def test_headlamp_rollout_does_not_deadlock_on_two_eligible_nodes():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    rolling_update = deployment["spec"]["strategy"]["rollingUpdate"]
    eligible_nodes = set(
        deployment["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]
        ["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]
        ["matchExpressions"][0]["values"]
    )

    assert deployment["spec"]["replicas"] == 2
    assert rolling_update == {"maxSurge": 0, "maxUnavailable": 1}
    assert eligible_nodes == {"restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-6dc3f140f9"}


def test_advanced_fabric_publishes_read_only_network_evidence():
    advanced = next(doc for doc in definitions() if doc["metadata"]["name"] == "advanced-fabrics")
    operations = set(advanced["spec"]["policy"]["allowedOperations"])
    assert {"node-network-status", "kernel-ecmp-routes", "bgp-candidate-paths",
            "path-decisions", "network-quality-status", "dns-quality-status",
            "network-intelligence-status"} <= operations
    assert {"data", "stringData", "secret"} <= set(advanced["spec"]["policy"]["deniedFields"])
