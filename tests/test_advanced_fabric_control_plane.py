from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "platform/advanced-fabric/release.yaml"


def release_values():
    documents = list(yaml.safe_load_all(RELEASE.read_text(encoding="utf-8")))
    helm_release = next(item for item in documents if item["kind"] == "HelmRelease")
    return helm_release["spec"]["values"]


def test_control_plane_vip_uses_all_three_etcd_hosts_as_guarded_origins():
    advanced = release_values()["advancedFabric"]
    api = advanced["controlPlaneApi"]
    expected = {"restricted-node-1eb0570f74", "restricted-node-296f36f2fb", "restricted-node-6dc3f140f9"}

    assert advanced["mode"] == "guarded-apply"
    assert advanced["applyEnabled"] is True
    assert advanced["weightedEcmp"]["enabled"] is True
    assert api["enabled"] is True
    assert api["vip"] == "192.0.2.220/32"
    assert api["mode"] == "healthy-ecmp"
    assert set(api["eligibleNodes"]) == expected
    assert set(api["guardedNodes"]) == expected
    operations = {item["name"]: item for item in api["nodeOperations"]}
    assert expected.issubset(operations)

    consumers = {
        "overseas-la": ["wg-pt-b1"],
        "overseas-kl": ["wg-pt-a1", "wg-pt-b1"],
        "vm-20-12-ubuntu": ["wg-pt-a1", "wg-pt-b1"],
        "suxunji": ["wg-pt-a1", "wg-pt-b1"],
        "muaya-lightweight": ["wg-pt-a1", "wg-pt-b1"],
        "muaya-ecs": ["wg-pt-a1", "wg-pt-b1"],
        "zan": ["wg-pt-a1", "wg-pt-b1"],
        "domain-ex": ["wg-pt-a1", "wg-pt-b1"],
    }
    for name, interfaces in consumers.items():
        assert operations[name]["wireguardInterfaces"] == interfaces
        assert operations[name]["wireguardAllowedPrefixes"] == ["192.0.2.220/32"]


def test_control_plane_rollout_does_not_enable_routeros_mutation():
    values = release_values()
    assert values["routerOS"]["enabled"] is False


def test_controller_runs_on_verified_control_plane_api_path():
    advanced = release_values()["advancedFabric"]
    assert advanced["controller"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-1eb0570f74"}
