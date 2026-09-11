from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "a1-wsl2-k3s-server-config.yaml": "192.0.2.242",
    "b1-wsl2-k3s-server-config.yaml": "192.0.2.1",
    "r640-k3s-server-config.yaml": "192.0.2.179",
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
            "destination": "192.0.2.65/24",
            "protocol": "tcp",
            "port": 2380,
        }]
        if node == "restricted-node-6dc3f140f9":
            expected.append({"source": address, "destination": "192.0.2.57/16"})
        assert operations[node]["sourceIdentityRules"] == expected


def test_etcd_migration_retains_only_legacy_client_listener_for_diagnostics():
    legacy = {
        "a1-wsl2-k3s-server-config.yaml": "192.0.2.176",
        "b1-wsl2-k3s-server-config.yaml": "192.0.2.4",
        "r640-k3s-server-config.yaml": "192.0.2.178",
    }
    for filename, address in legacy.items():
        config = yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / filename).read_text())
        args = config["etcd-arg"]
        assert any(item.startswith("listen-client-urls=") and f"https://{address}:2379" in item for item in args)
        assert not any(item.startswith("listen-peer-urls=") and f"https://{address}:2380" in item for item in args)
