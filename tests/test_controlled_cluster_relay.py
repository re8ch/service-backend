from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
HEADLAMP = ROOT / "platform" / "headlamp"


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


def test_subcluster_kubeconfigs_use_tls_verified_relay_and_refresh_tokens():
    expected = {
        "artc-kubeconfig.yaml": (16443, "artc-1"),
        "qwen-kubeconfig.yaml": (26443, "qwen-1"),
    }
    for filename, (port, tls_name) in expected.items():
        config_map = documents(filename)[0]
        template = yaml.safe_load(next(iter(config_map["data"].values())))
        cluster = template["clusters"][0]["cluster"]
        oidc = template["users"][0]["user"]["auth-provider"]["config"]

        assert cluster["server"] == f"https://controlled-cluster-relay.headlamp.svc:{port}"
        assert cluster["tls-server-name"] == tls_name
        assert "offline_access" in oidc["scope"].split(",")
