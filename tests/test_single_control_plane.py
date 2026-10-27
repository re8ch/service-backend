import json
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


def test_dr_runbook_has_a_restricted_document_reference() -> None:
    # The original fencing/restore requirements are checked during authenticated
    # migration acceptance; public CI must not fetch the private runbook body.
    path = "operations/network/docs/a1-single-control-plane-cutover.md"
    manifest = json.loads((ROOT / "contracts/private-operations-docs/sources.json").read_text())
    document = next(row for row in manifest["documents"] if row["path"] == path)
    assert document["url"] in (ROOT / path).read_text()


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
    assert values["controller"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["a1-wsl-zt", "r640"]
    operations = {item["name"] for item in values["controlPlaneApi"]["nodeOperations"]}
    assert {"r640", "a1-wsl-zt", "b1-wsl-zt"}.issubset(operations)
    assert "mubao-wsl" not in operations
    assert values["controlPlaneApi"]["eligibleNodes"] == ["r640", "a1-wsl-zt"]


def test_b1_prefers_direct_hnsc_lan_path_without_removing_advfab_relay() -> None:
    nodes = yaml.safe_load((ROOT / "inventory/platform/governance/nodes.yaml").read_text())["nodes"]
    by_id = {node["id"]: node for node in nodes}
    assert by_id["a1-wsl-zt"]["fabric"]["wslWireGuard"]["apiVipPaths"] == ["local-owner", "advfab-relay"]
    assert by_id["b1-wsl-zt"]["fabric"]["wslWireGuard"]["apiVipPaths"] == ["hnsc-host-lan-direct", "advfab-relay"]

    b1_route = (ROOT / "clusters/re8ch/components/ops/b1-wsl-lan-node-source-route.service.d-30-k3s-api-vip.conf").read_text()
    assert "10.250.0.1/32 dev wg-wsl-lan src 10.250.0.125" in b1_route


def test_b1_agent_ignores_server_config_and_has_valid_eviction_args() -> None:
    text = (ROOT / "clusters/re8ch/components/ops/b1-wsl2-k3s-agent.service").read_text()
    assert "--config /dev/null" in text
    assert "memory.available<2Gi" in text
    assert "memory.available\\<2Gi" not in text


def test_root_context_documents_use_authenticated_topology_reference() -> None:
    for relative in ["README.md", "catalogs/platform/modules/service-backend/MEMORY.md"]:
        text = (ROOT / relative).read_text()
        assert "contracts/network-topology/README.md" in text
        assert "a1-wsl-zt" not in text
        assert "b1-wsl-zt" not in text
        assert "https://10.250.0.1:6443" not in text
