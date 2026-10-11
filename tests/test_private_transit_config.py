import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "operations/network/scripts/private-transit-config.py"
SPEC = importlib.util.spec_from_file_location("private_transit_config", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inventory():
    return json.loads(
        (ROOT / "operations/network/manifests/network/private-transit/inventory.json").read_text()
    )


def fake_keys(inv):
    return {
        f"{node['name']}:{fabric}": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        for node in MODULE.nodes(inv)
        for fabric in MODULE.fabrics_for(inv, node)
    }


def test_r640_pod_cidr_is_allowed_by_b1_gateway():
    inv = inventory()
    b1 = MODULE.by_name(inv, "b1-wsl-zt")
    rendered = MODULE.render_wg(inv, b1, "b1", fake_keys(inv))
    r640 = rendered.split("# r640", 1)[1].split("[Peer]", 1)[0]
    assert "10.42.6.0/24" in r640


def test_gateway_pod_cidr_is_allowed_by_r640_spoke():
    inv = inventory()
    r640 = MODULE.by_name(inv, "r640")
    rendered = MODULE.render_wg(inv, r640, "b1", fake_keys(inv))
    assert "10.42.13.0/24" in rendered


def test_interface_directives_precede_all_peer_sections():
    inv = inventory()
    b1 = MODULE.by_name(inv, "b1-wsl-zt")
    rendered = MODULE.render_wg(inv, b1, "b1", fake_keys(inv))
    interface, peers = rendered.split("[Peer]", 1)
    assert "PostUp" in interface
    assert "PostUp" not in peers
    assert "PostDown" not in peers


def test_la1_and_route_servers_agree_on_private_transit_ports():
    inv = inventory()
    keys = fake_keys(inv)
    la1 = MODULE.by_name(inv, "overseas-la")
    for fabric in ("a1", "b1"):
        gateway = MODULE.gateway(inv, fabric)
        port = inv["fabrics"][fabric]["port"]
        la1_conf = MODULE.render_wg(inv, la1, fabric, keys)
        gateway_conf = MODULE.render_wg(inv, gateway, fabric, keys)
        assert f"ListenPort = {port}" in la1_conf
        assert f"Endpoint = {la1['publicIp']}:{port}" in gateway_conf
