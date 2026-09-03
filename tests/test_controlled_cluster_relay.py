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

        assert cluster["server"] == f"http://among-clusters-credential-broker.credential-broker.svc/k8s/byoc-pilot/{grant}"
        assert "token" not in user
        provider = user["auth-provider"]
        assert provider["name"] == "oidc"
        assert provider["config"]["client-id"] == "headlamp"
        assert provider["config"]["idp-issuer-url"] == "https://dex.re8ch.com"
        assert provider["config"]["scope"] == "openid,profile,email,groups,offline_access"
