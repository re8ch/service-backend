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

    assert "no neighbor 192.0.2.124 capability fqdn" in rendered
    assert "no neighbor 192.0.2.219 capability fqdn" not in rendered


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

    assert "network 192.0.2.140/24" in r640_frr
    assert "FROM-73 seq 20 permit 192.0.2.57/24" in r640_frr
    assert "neighbor 192.0.2.124 maximum-prefix 2 90 restart 5" in r640_frr
    assert "network 192.0.2.57/24" in vm_frr
    assert "FROM-128 seq 20 permit 192.0.2.140/24" in vm_frr
    assert "AllowedIPs = 192.0.2.124/32, 192.0.2.192/32, 192.0.2.57/24" in r640_wg


def test_pod_cidr_peering_must_be_reciprocal():
    data = inventory()
    PUBLIC_MESH.select(data, "restricted-node-6dc3f140f9")["podCIDRPeers"] = []

    with pytest.raises(ValueError, match="non-reciprocal podCIDR peer"):
        PUBLIC_MESH.validate(data)
