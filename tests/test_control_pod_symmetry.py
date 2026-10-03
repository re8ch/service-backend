import json
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
        "restricted-node-1eb0570f74": {"podCIDR": "192.0.2.109/24", "interface": "wg-cp-a1"},
        "restricted-node-296f36f2fb": {"podCIDR": "192.0.2.26/24", "interface": "wg-cp-b1"},
        "restricted-node-abb3f27a69": {"podCIDR": "192.0.2.214/24", "interface": "wg-cp-mubao"},
    }
