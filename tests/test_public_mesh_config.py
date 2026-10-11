import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/network/scripts/public-mesh-config.py"
SPEC = importlib.util.spec_from_file_location("public_mesh_config", SCRIPT)
PUBLIC_MESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLIC_MESH)


def inventory():
    return json.loads(
        (ROOT / "operations/network/manifests/network/public-mesh/inventory.json").read_text()
    )


def test_r640_disables_fqdn_capability_only_for_legacy_vm_peer():
    data = inventory()
    restricted-node-6dc3f140f9 = PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")
    rendered = PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=0)

    assert "no neighbor 192.0.2.248 capability fqdn" in rendered
    assert "no neighbor 192.0.2.117 capability fqdn" not in rendered


def test_vm_does_not_receive_newer_frr_fqdn_command():
    data = inventory()
    vm = PUBLIC_MESH.select(data, "vm-20-12-ubuntu")

    assert "capability fqdn" not in PUBLIC_MESH.render_frr(data, vm, stage=0)


def test_fqdn_compatibility_rejects_unknown_source():
    data = inventory()
    vm = PUBLIC_MESH.select(data, "vm-20-12-ubuntu")
    vm["peerCompatibility"]["disableFqdnCapabilityFrom"] = ["missing-node"]

    with pytest.raises(ValueError, match="invalid disableFqdnCapabilityFrom peer"):
        PUBLIC_MESH.validate(data)


def test_r640_and_vm_exchange_pod_cidrs_over_bfd_backed_public_mesh_bgp():
    data = inventory()
    restricted-node-6dc3f140f9 = PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")
    vm = PUBLIC_MESH.select(data, "vm-20-12-ubuntu")

    r640_frr = PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=0)
    vm_frr = PUBLIC_MESH.render_frr(data, vm, stage=0)
    r640_wg = PUBLIC_MESH.render_wireguard(
        data, restricted-node-6dc3f140f9, {"73": "vm-key"}, stage=0
    )

    assert "network 192.0.2.18/24" in r640_frr
    assert "FROM-73 seq 20 permit 192.0.2.224/24" in r640_frr
    assert "neighbor 192.0.2.248 maximum-prefix 2 90 restart 5" in r640_frr
    assert "network 192.0.2.224/24" in vm_frr
    assert "FROM-128 seq 20 permit 192.0.2.18/24" in vm_frr
    assert "AllowedIPs = 192.0.2.248/32, 192.0.2.67/32, 192.0.2.224/24" in r640_wg


def test_pod_cidr_peering_must_be_reciprocal():
    data = inventory()
    PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")["podCIDRPeers"] = []

    with pytest.raises(ValueError, match="non-reciprocal podCIDR peer"):
        PUBLIC_MESH.validate(data)


def test_expansion_stages_preserve_legacy_and_add_direct_spines():
    data = inventory()
    restricted-node-6dc3f140f9 = PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")
    flk = PUBLIC_MESH.select(data, "overseas-flk")
    assert "overseas-flk" not in PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=6)
    assert "neighbor 192.0.2.115 remote-as 4200000105" in PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=7)
    assert "neighbor 192.0.2.81 remote-as 4200000220" not in PUBLIC_MESH.render_frr(data, flk, stage=7)
    assert "neighbor 192.0.2.81 remote-as 4200000220" in PUBLIC_MESH.render_frr(data, flk, stage=8)
    assert "neighbor 192.0.2.115 remote-as" not in PUBLIC_MESH.render_frr(
        data, PUBLIC_MESH.select(data, "qianwen-nuclio-79"), stage=8)


def test_expanded_peers_exchange_accelerated_identities_and_declared_pod_cidrs():
    data = inventory()
    flk = PUBLIC_MESH.select(data, "overseas-flk")
    restricted-node-6dc3f140f9 = PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")
    keys = {node["id"]: f'key-{node["id"]}' for node in data["nodes"]}
    flk_frr = PUBLIC_MESH.render_frr(data, flk, stage=8)
    r640_frr = PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=8)
    flk_wg = PUBLIC_MESH.render_wireguard(data, flk, keys, stage=8)
    assert "network 192.0.2.109/32" in flk_frr
    assert "network 192.0.2.61/32" not in flk_frr
    assert "FROM-128 seq 10 permit 192.0.2.205/32" in flk_frr
    assert "neighbor 192.0.2.115 prefix-list TO-105 out" in r640_frr
    assert "TO-105 seq 20 permit 192.0.2.18/24" in r640_frr
    assert "FROM-105 seq 20 permit 192.0.2.254/24" in r640_frr
    assert "AllowedIPs = 192.0.2.145/32, 192.0.2.205/32, 192.0.2.18/24" in flk_wg
    assert "192.0.2.183/32" not in flk_wg


def test_expansion_rejects_unpaired_mesh_peer():
    data = inventory()
    PUBLIC_MESH.select(data, "overseas-la-2")["meshPeers"].remove("overseas-flk")
    with pytest.raises(ValueError, match="non-reciprocal mesh peer"):
        PUBLIC_MESH.validate(data)


def test_la1_api_vip_is_reachable_only_through_r640_peer():
    data = inventory()
    la1 = PUBLIC_MESH.select(data, "overseas-la")
    restricted-node-6dc3f140f9 = PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")
    keys = {node["id"]: f'key-{node["id"]}' for node in data["nodes"]}
    la1_wg = PUBLIC_MESH.render_wireguard(data, la1, keys, stage=10)
    la1_frr = PUBLIC_MESH.render_frr(data, la1, stage=10)
    r640_frr = PUBLIC_MESH.render_frr(data, restricted-node-6dc3f140f9, stage=10)

    assert "AllowedIPs = 192.0.2.145/32, 192.0.2.205/32, 192.0.2.18/24, 192.0.2.220/32" in la1_wg
    assert "FROM-128 seq 15 permit 192.0.2.220/32" in la1_frr
    assert "neighbor 192.0.2.145 maximum-prefix 3 90 restart 5" in la1_frr
    assert "TO-104 seq 20 permit 192.0.2.18/24" in r640_frr
    assert "TO-104 seq 30 permit 192.0.2.220/32" in r640_frr
    assert "neighbor 192.0.2.49 prefix-list TO-104 out" in r640_frr
    assert "neighbor 192.0.2.49 timers 3 9" in r640_frr


def test_mubao_dials_two_public_spines_without_advertising_zerotier():
    data = inventory()
    PUBLIC_MESH.validate(data)
    keys = {node["id"]: f'key-{node["id"]}' for node in data["nodes"]}
    mubao = PUBLIC_MESH.select(data, "restricted-node-abb3f27a69")
    flk = PUBLIC_MESH.select(data, "overseas-flk")
    mubao_wg = PUBLIC_MESH.render_wireguard(data, mubao, keys, stage=9)
    flk_wg = PUBLIC_MESH.render_wireguard(data, flk, keys, stage=9)
    mubao_frr = PUBLIC_MESH.render_frr(data, mubao, stage=9)
    assert "MTU = 1120" in mubao_wg
    assert "Endpoint = 192.0.2.101:51888" in mubao_wg
    assert "Endpoint = 192.0.2.38:51888" in mubao_wg
    assert "Endpoint = None" not in flk_wg
    assert "AllowedIPs = 192.0.2.79/32, 192.0.2.114/32" in flk_wg
    assert "network 192.0.2.114/32" in mubao_frr
    assert "network 192.0.2.61/32" not in mubao_frr
