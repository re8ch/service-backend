#!/usr/bin/env python3
import ipaddress
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "operations/host-ops/management-ssh-mesh/inventory.json"


def main() -> None:
    document = json.loads(INVENTORY.read_text())
    spec = document["spec"]
    nodes = spec["nodes"]
    names = {node["name"] for node in nodes}
    expected = {"mikrotik", "a1-windows", "b1-windows"}
    if names != expected:
        raise SystemExit(f"management mesh nodes must be {sorted(expected)}")

    for node in nodes:
        ssh = node["ssh"]
        if ssh["port"] != 22:
            raise SystemExit(f"{node['name']} SSH port must be 22")
        if not ssh.get("hostKeyRef"):
            raise SystemExit(f"{node['name']} must pin a hostKeyRef")
        for address in ssh["addresses"]:
            ipaddress.ip_address(address)

    windows = [node for node in nodes if node["platform"] == "windows-server-2022"]
    if len(windows) != 2:
        raise SystemExit("exactly two Windows control-plane hosts are required")
    if len({node["peerPriority"] for node in windows}) != 2:
        raise SystemExit("Windows peerPriority values must be distinct for staggered recovery")

    watchdog = spec["watchdog"]
    if watchdog["bootGraceSeconds"] < 600:
        raise SystemExit("boot grace must allow Windows, WSL and k3s startup")
    if watchdog["hostRebootCooldownSeconds"] < 21600:
        raise SystemExit("host reboot cooldown must be at least six hours")
    if watchdog["nodeReplyTimeoutSeconds"] < 300:
        raise SystemExit("node reply timeout must allow WSL and k3s to recover")
    if not watchdog["peerQuorumRequired"]:
        raise SystemExit("automatic host reboot must require a healthy etcd peer")
    if watchdog["minimumHealthyPeers"] != 2:
        raise SystemExit("automatic host reboot must require both other etcd voters")
    if watchdog["priorityDelaySeconds"] < 30:
        raise SystemExit("priority delay must create a meaningful stagger window")

    for cidr in spec["allowedSourceCidrs"]:
        ipaddress.ip_network(cidr)
    for value in spec["credentialRefs"].values():
        if not value or "password" in value.lower():
            raise SystemExit("credentialRefs must be opaque non-password references")
    print("management SSH mesh inventory validated")


if __name__ == "__main__":
    main()
