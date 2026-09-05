from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
HEADLAMP = ROOT / "platform" / "headlamp"
AI_OPERATIONS = ROOT / "clusters" / "re8ch" / "components" / "ai-operations" / "platform.yaml"


def documents(name):
    return list(yaml.safe_load_all((HEADLAMP / name).read_text()))


def test_relay_is_pinned_to_the_authoritative_a1_entrypoint():
    relay = next(item for item in documents("controlled-cluster-relay.yaml") if item["kind"] == "Deployment")
    spec = relay["spec"]["template"]["spec"]

    assert spec["nodeSelector"]["kubernetes.io/hostname"] == "restricted-node-1eb0570f74"
    assert "@sha256:" in spec["containers"][0]["image"]


def test_relay_only_publishes_the_two_controlled_api_ports():
    relay = next(item for item in documents("controlled-cluster-relay.yaml") if item["kind"] == "Service")

    assert {(port["name"], port["port"]) for port in relay["spec"]["ports"]} == {
        ("artc-api", 16443),
        ("qwen-api", 26443),
    }


def test_subcluster_kubeconfigs_use_broker_imports_and_opaque_references():
    expected = {
        "artc-kubeconfig.yaml": "artc-headlamp-admin",
        "qwen-kubeconfig.yaml": "qwen-headlamp-admin",
    }
    for filename, grant in expected.items():
        config_map = documents(filename)[0]
        template = yaml.safe_load(next(iter(config_map["data"].values())))
        cluster = template["clusters"][0]["cluster"]
        user = template["users"][0]["user"]

        assert cluster["server"] == "https://among-clusters.re8ch.com"
        assert user["token"] == f"credential://among-clusters/byoc-pilot/{grant}"
        assert "auth-provider" not in user


def test_cluster_tool_kubeconfigs_use_native_origin_and_opaque_references():
    config_map = next(
        item
        for item in yaml.safe_load_all(AI_OPERATIONS.read_text())
        if item and item.get("kind") == "ConfigMap" and item["metadata"]["name"] == "controlled-cluster-kubeconfigs"
    )
    for cluster, grant in (("qwen", "qwen-headlamp-admin"), ("artc", "artc-headlamp-admin")):
        kubeconfig = yaml.safe_load(config_map["data"][f"{cluster}-controlled.yaml"])
        assert kubeconfig["clusters"][0]["cluster"]["server"] == "https://among-clusters.re8ch.com"
        assert kubeconfig["users"][0]["user"]["token"] == f"credential://among-clusters/byoc-pilot/{grant}"
