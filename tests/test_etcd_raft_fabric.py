from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "a1-wsl2-k3s-server-config.yaml": "192.0.2.173",
    "b1-wsl2-k3s-server-config.yaml": "192.0.2.213",
    "r640-k3s-server-config.yaml": "192.0.2.205",
}
def test_etcd_members_advertise_node_owned_advanced_fabric_identities():
    for filename, address in CONFIGS.items():
        config = yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / filename).read_text())
        args = set(config["etcd-arg"])

        assert config["node-ip"] == address
        assert f"advertise-client-urls=https://{address}:2379" in args
        assert f"initial-advertise-peer-urls=https://{address}:2380" in args
        assert any(item.startswith("listen-client-urls=") and f"https://{address}:2379" in item for item in args)
        assert any(item.startswith("listen-peer-urls=") and f"https://{address}:2380" in item for item in args)
        assert address in config["tls-san"]


def test_etcd_peer_traffic_preserves_node_owned_advanced_fabric_source_identity():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {
        item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]
    }
    for filename, address in CONFIGS.items():
        node = filename.removesuffix("-k3s-server-config.yaml").replace("-wsl2", "-wsl-zt")
        expected = [{
            "source": address,
            "destination": "192.0.2.106/24",
            "protocol": "tcp",
            "port": 2380,
        }, {"source": address, "destination": "192.0.2.106/24"}]
        assert operations[node]["sourceIdentityRules"] == expected


def test_pod_to_pod_traffic_preserves_cilium_source_identity_on_every_fabric_node():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {
        item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]
    }
    for node in operations:
        assert all(rule["destination"] != "192.0.2.224/16" for rule in operations[node].get("sourceIdentityRules", []))


def test_advanced_fabric_release_validation_does_not_depend_on_r640():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    assert values["advancedFabric"]["dns"]["testNodeSelector"] == {
        "kubernetes.io/hostname": "restricted-node-1eb0570f74"
    }


def test_reachable_nodes_receive_full_advanced_fabric_identity_transactions():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]}
    identities = {"amdwsl-zt": "192.0.2.93", "domain-ex": "192.0.2.31", "muaya-ecs": "192.0.2.46",
                  "muaya-lightweight": "192.0.2.24", "overseas-la": "192.0.2.29", "suxunji": "192.0.2.145",
                  "vm-20-12-ubuntu": "192.0.2.114", "zan": "192.0.2.63"}
    for node, identity in identities.items():
        assert operations[node]["wireguardAllowedPrefixes"] == ["192.0.2.106/24", "192.0.2.215/24"]
        assert {"source": identity, "destination": "192.0.2.106/24"} in operations[node]["sourceIdentityRules"]
        assert all(rule["destination"] != "192.0.2.224/16" for rule in operations[node]["sourceIdentityRules"])
    assert operations["overseas-kl"]["wireguardAllowedPrefixes"] == ["192.0.2.220/32"]


def test_etcd_migration_retains_only_legacy_client_listener_for_diagnostics():
    legacy = {
        "a1-wsl2-k3s-server-config.yaml": "192.0.2.18",
        "b1-wsl2-k3s-server-config.yaml": "192.0.2.208",
        "r640-k3s-server-config.yaml": "192.0.2.183",
    }
    for filename, address in legacy.items():
        config = yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / filename).read_text())
        args = config["etcd-arg"]
        assert any(item.startswith("listen-client-urls=") and f"https://{address}:2379" in item for item in args)
        assert not any(item.startswith("listen-peer-urls=") and f"https://{address}:2380" in item for item in args)
