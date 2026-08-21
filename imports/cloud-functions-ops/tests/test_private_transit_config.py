import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "network-operations/scripts/private-transit-config.py"
SPEC = importlib.util.spec_from_file_location("private_transit_config", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inventory():
    return json.loads(
        (ROOT / "network-operations/manifests/network/private-transit/inventory.json").read_text()
    )


def fake_keys(inv):
    return {
        f"{node['name']}:{fabric}": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        for node in MODULE.nodes(inv)
        for fabric in MODULE.fabrics_for(inv, node)
    }


def test_r640_pod_cidr_is_allowed_by_b1_gateway():
    inv = inventory()
    b1 = MODULE.by_name(inv, "restricted-node-296f36f2fb")
    rendered = MODULE.render_wg(inv, b1, "b1", fake_keys(inv))
    restricted-node-6dc3f140f9 = rendered.split("# restricted-node-6dc3f140f9", 1)[1].split("[Peer]", 1)[0]
    assert "192.0.2.140/24" in restricted-node-6dc3f140f9


def test_gateway_pod_cidr_is_allowed_by_r640_spoke():
    inv = inventory()
    restricted-node-6dc3f140f9 = MODULE.by_name(inv, "restricted-node-6dc3f140f9")
    rendered = MODULE.render_wg(inv, restricted-node-6dc3f140f9, "b1", fake_keys(inv))
    assert "192.0.2.234/24" in rendered


def test_interface_directives_precede_all_peer_sections():
    inv = inventory()
    b1 = MODULE.by_name(inv, "restricted-node-296f36f2fb")
    rendered = MODULE.render_wg(inv, b1, "b1", fake_keys(inv))
    interface, peers = rendered.split("[Peer]", 1)
    assert "PostUp" in interface
    assert "PostUp" not in peers
    assert "PostDown" not in peers
