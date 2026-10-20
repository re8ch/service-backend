import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_single_control_plane_validator() -> None:
    result = subprocess.run(
        ["python", "operations/scripts/validate-single-control-plane.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "validated" in result.stdout


def test_dr_runbook_requires_fencing_before_b1_promotion() -> None:
    text = (ROOT / "operations/network/docs/a1-single-control-plane-cutover.md").read_text()
    assert "proof that A1 is powered off" in text
    assert "At no point may A1" in text
    assert "--cluster-reset" in text


def test_backup_copy_encrypts_snapshot_and_token() -> None:
    text = (ROOT / "clusters/re8ch/components/ops/a1-etcd-backup-to-r640").read_text()
    assert text.count("age -r") == 2
    assert "server/token" in text
    assert "sha256sum" in text
    assert "/srv/re8ch-backup/k3s/a1" in text


def test_advfab_controller_and_b1_data_plane_are_preserved() -> None:
    release = list(yaml.safe_load_all((ROOT / "platform/advanced-fabric/release.yaml").read_text()))
    values = next(item for item in release if item and item.get("kind") == "HelmRelease")["spec"]["values"]["advancedFabric"]
    assert values["controller"]["replicas"] == 2
    assert values["controller"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["restricted-node-1eb0570f74", "restricted-node-6dc3f140f9"]
    operations = {item["name"] for item in values["controlPlaneApi"]["nodeOperations"]}
    assert {"restricted-node-6dc3f140f9", "restricted-node-1eb0570f74", "restricted-node-296f36f2fb"}.issubset(operations)
    assert "restricted-node-abb3f27a69" not in operations
    assert values["controlPlaneApi"]["eligibleNodes"] == ["restricted-node-6dc3f140f9", "restricted-node-1eb0570f74"]


def test_b1_prefers_direct_hnsc_lan_path_without_removing_advfab_relay() -> None:
    nodes = yaml.safe_load((ROOT / "inventory/platform/governance/nodes.yaml").read_text())["nodes"]
    by_id = {node["id"]: node for node in nodes}
    assert by_id["restricted-node-1eb0570f74"]["fabric"]["wslWireGuard"]["apiVipPaths"] == ["local-owner", "advfab-relay"]
    assert by_id["restricted-node-296f36f2fb"]["fabric"]["wslWireGuard"]["apiVipPaths"] == ["hnsc-host-lan-direct", "advfab-relay"]

    b1_route = (ROOT / "clusters/re8ch/components/ops/b1-wsl-lan-node-source-route.service.d-30-k3s-api-vip.conf").read_text()
    assert "192.0.2.220/32 dev wg-wsl-lan src 192.0.2.213" in b1_route


def test_b1_agent_ignores_server_config_and_has_valid_eviction_args() -> None:
    text = (ROOT / "clusters/re8ch/components/ops/b1-wsl2-k3s-agent.service").read_text()
    assert "--config /dev/null" in text
    assert "memory.available<2Gi" in text
    assert "memory.available\\<2Gi" not in text
