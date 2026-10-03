import json
import importlib.util
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/network/scripts/routeros/control_pod_mesh.py"
INVENTORY = ROOT / "operations/network/manifests/network/private-transit/routeros-control-mesh.json"


def test_three_control_pod_routes_have_distinct_wg_endpoints():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(INVENTORY), "--action", "validate"],
        check=True, capture_output=True, text=True,
    )
    matrix = json.loads(result.stdout)
    assert matrix == {
        "restricted-node-1eb0570f74": {"podCIDR": "192.0.2.48/24", "interface": "wg-cp-a1"},
        "restricted-node-296f36f2fb": {"podCIDR": "192.0.2.234/24", "interface": "wg-cp-b1"},
        "restricted-node-abb3f27a69": {"podCIDR": "192.0.2.113/24", "interface": "wg-cp-mubao"},
    }


def test_systemd_route_unit_tracks_wireguard_lifecycle():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("control_pod_mesh", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    unit = module.render_unit("restricted-node-abb3f27a69", "wg-cp-mubao")
    assert "Requires=wg-quick@wg-cp-mubao.service" in unit
    assert "BindsTo=wg-quick@wg-cp-mubao.service" in unit
    assert "--node restricted-node-abb3f27a69 --action stage-node" in unit
    assert "--node restricted-node-abb3f27a69 --action rollback-node" in unit


def test_routeros_pod_route_is_a_policy_fallback(monkeypatch):
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("control_pod_mesh", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory = json.loads(INVENTORY.read_text())
    links = {item["node"]: item for item in inventory["links"]}
    calls = []

    def fake_run(*args):
        calls.append(args)
        if args[:4] == ("wg", "show", "wg-cp-a1", "allowed-ips"):
            return links["restricted-node-1eb0570f74"]["routerPublicKey"] + " 192.0.2.78/32"
        return ""

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    module.node_action("stage-node", "restricted-node-1eb0570f74", links, inventory["podRoutePolicy"])

    pod = "192.0.2.234/24"
    assert ("ip", "route", "replace", pod, "via", "192.0.2.78", "dev", "wg-cp-a1",
            "src", "192.0.2.242", "metric", "250", "table", "15425") in calls
    assert ("ip", "rule", "add", "pref", "11476", "to", pod,
            "lookup", "main", "suppress_prefixlength", "23") in calls
    assert ("ip", "rule", "add", "pref", "11477", "to", pod,
            "lookup", "15425") in calls
    assert not any(call[:4] == ("ip", "route", "replace", pod) and "table" not in call
                   for call in calls)
    assert ("ip", "route", "replace", "192.0.2.113/24", "via", "192.0.2.78",
            "dev", "wg-cp-a1", "src", "192.0.2.242", "metric", "10") in calls
