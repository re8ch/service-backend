#!/usr/bin/env python3
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
VIP = "https://192.0.2.220:6443"


def main() -> None:
    clusters = yaml.safe_load((ROOT / "inventory/platform/governance/clusters.yaml").read_text())
    cluster = next(item for item in clusters["clusters"] if item["id"] == "re8ch-k3s")
    assert cluster["controlPlaneNodes"] == ["restricted-node-1eb0570f74"]
    assert cluster["warmStandbyNodes"] == ["restricted-node-296f36f2fb"]
    assert cluster["backupPolicy"] == "hourly-etcd-snapshot-encrypted-r640-copy"

    nodes = yaml.safe_load((ROOT / "inventory/platform/governance/nodes.yaml").read_text())["nodes"]
    roles = {node["id"]: node["roles"] for node in nodes}
    assert roles["restricted-node-1eb0570f74"] == ["control-plane", "etcd"]
    assert roles["restricted-node-296f36f2fb"] == ["worker", "manual-dr-standby"]
    assert "control-plane" not in roles["restricted-node-abb3f27a69"] and "etcd" not in roles["restricted-node-abb3f27a69"]
    assert "api-relay" in roles["restricted-node-6dc3f140f9"]

    a1 = yaml.safe_load((ROOT / "clusters/re8ch/components/ops/a1-wsl2-k3s-server-config.yaml").read_text())
    assert a1["cluster-init"] is True and "server" not in a1
    assert a1["etcd-snapshot-schedule-cron"] == "0 * * * *"
    assert a1["etcd-snapshot-retention"] >= 24
    assert "192.0.2.220" in a1["tls-san"]

    agent_texts = [
        (ROOT / "clusters/re8ch/components/ops/a1-wsl2-k3s-agent.service").read_text(),
        (ROOT / "clusters/re8ch/components/ops/b1-wsl2-k3s-agent.service").read_text(),
        (ROOT / "clusters/re8ch/components/systemd/k3s-agent-icp.service").read_text(),
        (ROOT / "operations/network/manifests/network/private-transit/r640-k3s-agent-config.yaml").read_text(),
    ]
    assert all(VIP in text for text in agent_texts)

    relay = (ROOT / "operations/network/manifests/network/private-transit/r640-k3s-api-relay.cfg").read_text()
    assert "server a1 192.0.2.173:6443" in relay
    assert "server b1 192.0.2.213:6443" in relay and "disabled" in relay
    assert "server mubao" not in relay

    mesh = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = {node["name"]: node for node in mesh["spec"]["nodes"] if node["platform"].startswith("windows")}
    assert windows["a1-windows"]["watchdog"]["serviceName"] == "k3s.service"
    assert windows["b1-windows"]["watchdog"] == {"role": "worker-standby", "serviceName": "k3s-agent.service"}

    print("single control-plane desired state validated")


if __name__ == "__main__":
    main()
