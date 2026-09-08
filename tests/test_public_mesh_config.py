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
