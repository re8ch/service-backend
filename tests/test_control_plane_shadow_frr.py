import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "operations/network/scripts/routeros"
sys.path.insert(0, str(SCRIPTS))
import control_plane_shadow_frr as shadow  # noqa: E402


@pytest.mark.parametrize("mtu,expected", [(1200, 200), (1280, None)])
def test_promotion_requires_measured_mtu_and_updates_exact_route_map(monkeypatch, mtu, expected):
    inventory = ROOT / "operations/network/manifests/network/private-transit/routeros-control-mesh.json"
    monkeypatch.setattr(sys, "argv", ["control_plane_shadow_frr.py", "--inventory", str(inventory),
                                   "--node", "restricted-node-abb3f27a69", "--action", "promote"])
    monkeypatch.setattr(shadow.os, "geteuid", lambda: 0)
    monkeypatch.setattr(shadow, "run", lambda *args: "via 192.0.2.47 dev wg-cp-mubao")
    original_read_text = pathlib.Path.read_text
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *args, **kwargs: (
        str(mtu) if str(self) == "/sys/class/net/wg-cp-mubao/mtu"
        else original_read_text(self, *args, **kwargs)))
    commands = []

    def vtysh(args):
        if args == ["show running-config"]:
            return "neighbor 192.0.2.47 route-map ROS-CP-PROBE-IN in\n" \
                   "neighbor 192.0.2.47 route-map ROS-CP-PROBE-OUT out\n" \
                   "route-map ROS-CP-PROBE-IN permit 20\n"
        commands.append(args)
        return ""

    monkeypatch.setattr(shadow, "vtysh", vtysh)
    if expected is None:
        with pytest.raises(RuntimeError, match="MTU exceeds"):
            shadow.main()
        assert commands == []
    else:
        shadow.main()
        assert ["configure terminal", "route-map ROS-CP-PROBE-IN permit 20",
                "set local-preference 200", "end", "write memory"] in commands
