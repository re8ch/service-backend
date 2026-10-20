#!/usr/bin/env python3
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
VIP = "https://10.250.0.1:6443"


def main() -> None:
    clusters = yaml.safe_load((ROOT / "inventory/platform/governance/clusters.yaml").read_text())
    cluster = next(item for item in clusters["clusters"] if item["id"] == "re8ch-k3s")
    assert cluster["controlPlaneNodes"] == ["a1-wsl-zt"]
    assert cluster["warmStandbyNodes"] == ["b1-wsl-zt"]
    assert cluster["backupPolicy"] == "hourly-etcd-snapshot-encrypted-r640-copy"

    nodes = yaml.safe_load((ROOT / "inventory/platform/governance/nodes.yaml").read_text())["nodes"]
    roles = {node["id"]: node["roles"] for node in nodes}
    assert roles["a1-wsl-zt"] == ["control-plane", "etcd"]
    assert roles["b1-wsl-zt"] == ["worker", "manual-dr-standby"]
    assert "control-plane" not in roles["mubao-wsl"] and "etcd" not in roles["mubao-wsl"]
    assert "api-relay" in roles["r640"]

    fabrics = {node["id"]: node.get("fabric", {}) for node in nodes}
    assert fabrics["a1-wsl-zt"]["wslWireGuard"]["apiVipPaths"] == ["local-owner", "advfab-relay"]
    assert fabrics["b1-wsl-zt"]["wslWireGuard"]["apiVipPaths"] == ["hnsc-host-lan-direct", "advfab-relay"]

    a1 = yaml.safe_load((ROOT / "clusters/re8ch/components/ops/a1-wsl2-k3s-server-config.yaml").read_text())
    assert a1["cluster-init"] is True and "server" not in a1
    assert a1["etcd-snapshot-schedule-cron"] == "0 * * * *"
    assert a1["etcd-snapshot-retention"] >= 24
    assert "10.250.0.1" in a1["tls-san"]

    agent_texts = [
        (ROOT / "clusters/re8ch/components/ops/a1-wsl2-k3s-agent.service").read_text(),
        (ROOT / "clusters/re8ch/components/ops/b1-wsl2-k3s-agent.service").read_text(),
        (ROOT / "clusters/re8ch/components/systemd/k3s-agent-icp.service").read_text(),
        (ROOT / "operations/network/manifests/network/private-transit/r640-k3s-agent-config.yaml").read_text(),
    ]
    assert all(VIP in text for text in agent_texts)

    b1_agent = agent_texts[1]
    assert "--config /dev/null" in b1_agent
    assert "memory.available<2Gi" in b1_agent
    assert "memory.available\\<2Gi" not in b1_agent

    a1_vip = (ROOT / "clusters/re8ch/components/ops/a1-wsl-lan-node-source-route.service.d-30-k3s-api-vip.conf").read_text()
    b1_vip = (ROOT / "clusters/re8ch/components/ops/b1-wsl-lan-node-source-route.service.d-30-k3s-api-vip.conf").read_text()
    assert "address replace 10.250.0.1/32 dev lo" in a1_vip
    assert "table 15300 10.250.0.1/32 dev wg-wsl-lan src 10.250.0.125" in b1_vip

    relay = (ROOT / "operations/network/manifests/network/private-transit/r640-k3s-api-relay.cfg").read_text()
    assert "server a1 10.250.0.147:6443" in relay
    assert "server b1 10.250.0.125:6443" in relay and "disabled" in relay
    assert "server mubao" not in relay

    mesh = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = {node["name"]: node for node in mesh["spec"]["nodes"] if node["platform"].startswith("windows")}
    assert windows["a1-windows"]["watchdog"]["serviceName"] == "k3s.service"
    assert windows["b1-windows"]["watchdog"] == {"role": "worker-standby", "serviceName": "k3s-agent.service"}

    print("single control-plane desired state validated")


if __name__ == "__main__":
    main()
