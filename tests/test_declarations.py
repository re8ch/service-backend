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


def test_headlamp_rollout_does_not_deadlock_on_stable_core_nodes():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    eligible_nodes = set(
        deployment["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]
        ["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]
        ["matchExpressions"][0]["values"]
    )

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    assert eligible_nodes == {"restricted-node-6dc3f140f9", "restricted-node-1eb0570f74", "restricted-node-296f36f2fb"}
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["hostNetwork"] is True
    assert pod_spec["dnsPolicy"] == "ClusterFirstWithHostNet"
    pod_anti_affinity = deployment["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in pod_anti_affinity
    preferred = pod_anti_affinity["preferredDuringSchedulingIgnoredDuringExecution"]
    assert preferred[0]["weight"] == 100
    plugins_volume = next(
        volume for volume in deployment["spec"]["template"]["spec"]["volumes"]
        if volume["name"] == "plugins"
    )
    assert plugins_volume == {"name": "plugins", "emptyDir": {}}


def test_headlamp_uses_stable_control_plane_api_endpoint():
    deployment = yaml.safe_load((ROOT / "platform/headlamp/deployment.yaml").read_text())
    headlamp = next(
        container for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == "headlamp"
    )
    environment = {
        item["name"]: item.get("value")
        for item in headlamp["env"]
    }

    assert environment["KUBERNETES_SERVICE_HOST"] == "127.0.0.1"
    assert environment["KUBERNETES_SERVICE_PORT"] == "6443"
    assert environment["KUBERNETES_SERVICE_PORT_HTTPS"] == "6443"


def test_advanced_fabric_publishes_read_only_network_evidence():
    advanced = next(doc for doc in definitions() if doc["metadata"]["name"] == "advanced-fabrics")
    operations = set(advanced["spec"]["policy"]["allowedOperations"])
    assert {"node-network-status", "kernel-ecmp-routes", "bgp-candidate-paths",
            "path-decisions", "network-quality-status", "dns-quality-status",
            "network-intelligence-status"} <= operations
    assert {"data", "stringData", "secret"} <= set(advanced["spec"]["policy"]["deniedFields"])
