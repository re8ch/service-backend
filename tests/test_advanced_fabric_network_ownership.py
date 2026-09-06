from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/advanced-fabric/network-helm-ownership.yaml"


def objects():
    return [item for item in yaml.safe_load_all(MANIFEST.read_text()) if item]


def releases():
    return {item["metadata"]["name"]: item for item in objects() if item["kind"] == "HelmRelease"}


def test_network_releases_are_declared_but_suspended_during_owner_cutover():
    assert set(releases()) == {"cilium", "traefik"}
    assert all(item["spec"]["suspend"] is True for item in releases().values())
    assert all(item["metadata"]["annotations"]["gitops.re8ch.com/adoption-phase"] == "declared-suspended" for item in releases().values())


def test_existing_helm_release_identity_is_preserved():
    for name, release in releases().items():
        assert release["spec"]["releaseName"] == name
        assert release["spec"]["targetNamespace"] == "kube-system"
        assert release["spec"]["storageNamespace"] == "kube-system"


def test_cilium_uses_the_running_immutable_dev_chart_and_digest_images():
    cilium = releases()["cilium"]
    assert cilium["spec"]["chart"]["spec"]["chart"].endswith("cilium-1.21.0-dev.tgz")
    values = cilium["spec"]["values"]
    assert values["image"]["useDigest"] is True
    assert values["image"]["digest"].startswith("sha256:")
    assert values["routingMode"] == "native"
    assert values["ipv4NativeRoutingCIDR"] == "192.0.2.57/16"


def test_traefik_uses_an_exact_external_chart_and_current_edge_placement():
    source = next(item for item in objects() if item["kind"] == "HelmRepository")
    assert source["spec"]["url"] == "https://traefik.github.io/charts"
    traefik = releases()["traefik"]
    assert traefik["spec"]["chart"]["spec"]["version"] == "39.0.7"
    values = traefik["spec"]["values"]
    assert values["hostNetwork"] is True
    assert values["nodeSelector"] == {"kubernetes.io/hostname": "muaya-lightweight"}
