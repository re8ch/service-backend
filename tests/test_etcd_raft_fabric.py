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
        assert operations[node]["sourceIdentityRules"] == [{
            "source": address,
            "destination": "192.0.2.106/24",
            "protocol": "tcp",
            "port": 2380,
        }]


def test_etcd_migration_retains_legacy_listeners_for_rollback():
    legacy = {
        "a1-wsl2-k3s-server-config.yaml": "192.0.2.18",
        "b1-wsl2-k3s-server-config.yaml": "192.0.2.208",
        "r640-k3s-server-config.yaml": "192.0.2.183",
    }
    for filename, address in legacy.items():
        config = yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / filename).read_text())
        args = config["etcd-arg"]
        assert any(item.startswith("listen-client-urls=") and f"https://{address}:2379" in item for item in args)
        assert any(item.startswith("listen-peer-urls=") and f"https://{address}:2380" in item for item in args)
