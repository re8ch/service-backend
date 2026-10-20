from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "clusters/re8ch/components/ops/a1-wsl2-k3s-server-config.yaml": ("a1-wsl-zt", "10.250.0.147"),
}
def test_etcd_members_advertise_node_owned_advanced_fabric_identities():
    for filename, (_, address) in CONFIGS.items():
        config = yaml.safe_load((ROOT / filename).read_text())
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
    # MUBAO uses the RouterOS control mesh; these source rules belong to A1/B1.
    for _, (node, address) in CONFIGS.items():
        if node == "mubao-wsl":
            continue
        expected = [{
            "source": address,
            "destination": "10.250.0.0/24",
            "protocol": "tcp",
            "port": 2380,
        }, {"source": address, "destination": "10.250.0.0/24"}]
        assert operations[node]["sourceIdentityRules"] == expected


def test_ab_control_plane_flows_bypass_broad_dynamic_ecmp_policy():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {
        item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]
    }
    expected = [
        {
            "priority": 11520,
            "destination": "10.250.0.0/24",
            "protocol": "tcp",
            "destinationPort": "2379-2380",
            "table": "main",
        },
        {
            "priority": 11521,
            "destination": "10.250.0.0/24",
            "protocol": "tcp",
            "destinationPort": "6443",
            "table": "main",
        },
    ]
    assert operations["a1-wsl-zt"]["routingPolicyRules"] == expected
    assert operations["b1-wsl-zt"]["routingPolicyRules"] == expected


def test_pod_to_pod_traffic_preserves_cilium_source_identity_on_every_fabric_node():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {
        item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]
    }
    for node in operations:
        assert all(rule["destination"] != "10.42.0.0/16" for rule in operations[node].get("sourceIdentityRules", []))


def test_advanced_fabric_release_validation_does_not_depend_on_r640():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    assert values["advancedFabric"]["dns"]["testNodeSelector"] == {
        "kubernetes.io/hostname": "r640"
    }


def test_reachable_nodes_receive_full_advanced_fabric_identity_transactions():
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(document for document in release if document["kind"] == "HelmRelease")["spec"]["values"]
    operations = {item["name"]: item for item in values["advancedFabric"]["controlPlaneApi"]["nodeOperations"]}
    identities = {"amdwsl-zt": "10.250.0.38", "domain-ex": "10.250.0.186", "muaya-ecs": "10.250.0.95",
                  "muaya-lightweight": "10.250.0.79", "overseas-la": "10.250.0.104", "suxunji": "10.250.0.77",
                  "vm-20-12-ubuntu": "10.250.0.73", "zan": "10.250.0.16"}
    for node, identity in identities.items():
        assert operations[node]["wireguardAllowedPrefixes"] == ["10.250.0.0/24", "10.251.0.0/24"]
        assert {"source": identity, "destination": "10.250.0.0/24"} in operations[node]["sourceIdentityRules"]
        assert all(rule["destination"] != "10.42.0.0/16" for rule in operations[node]["sourceIdentityRules"])
    assert operations["overseas-kl"]["wireguardAllowedPrefixes"] == ["10.250.0.1/32"]


def test_etcd_migration_retains_only_legacy_client_listener_for_diagnostics():
    legacy = {
        "a1-wsl2-k3s-server-config.yaml": "10.181.22.147",
    }
    for filename, address in legacy.items():
        config = yaml.safe_load((ROOT / "clusters/re8ch/components/ops" / filename).read_text())
        args = config["etcd-arg"]
        assert any(item.startswith("listen-client-urls=") and f"https://{address}:2379" in item for item in args)
        assert not any(item.startswith("listen-peer-urls=") and f"https://{address}:2380" in item for item in args)
