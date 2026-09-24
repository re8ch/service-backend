import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_management_ssh_mesh_validator() -> None:
    result = subprocess.run(
        ["python", "operations/scripts/validate-management-ssh-mesh.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "validated" in result.stdout


def test_host_reboot_is_quorum_guarded_and_staggered() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    spec = inventory["spec"]
    windows = [node for node in spec["nodes"] if node["platform"].startswith("windows")]
    assert spec["watchdog"]["peerQuorumRequired"] is True
    assert spec["watchdog"]["minimumHealthyPeers"] == 2
    assert spec["watchdog"]["bootGraceSeconds"] >= 600
    assert spec["watchdog"]["nodeReplyTimeoutSeconds"] >= 300
    assert 10 <= spec["watchdog"]["localWslProbeTimeoutSeconds"] <= 60
    assert spec["watchdog"]["hostRebootCooldownSeconds"] >= 21600
    assert len({node["peerPriority"] for node in windows}) == len(windows)


def test_no_secret_material_in_management_inventory() -> None:
    text = (ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text().lower()
    assert "privatekey" not in text
    assert '"password"' not in text
    assert "calvin" not in text


def test_windows_watchdog_uses_quorum_and_cooldown_guards() -> None:
    script = (ROOT / "operations/host-ops/windows/re8ch-control-plane-watchdog.ps1").read_text()
    assert "minimumHealthyPeers" in script
    assert "hostRebootDelaySeconds" in script
    assert "hostRebootCooldownSeconds" in script
    assert "nodeReplyTimeoutSeconds" in script
    assert "WaitForExit($timeoutMilliseconds)" in script
    assert "Kill($true)" in script
    assert "shutdown.exe" in script


def test_windows_installer_separates_wsl_owner_from_host_reboot_authority() -> None:
    script = (ROOT / "operations/host-ops/windows/install-re8ch-control-plane-host.ps1").read_text()
    assert "-UserId 'SYSTEM'" in script
    assert "-LogonType ServiceAccount" in script
    assert "OpenSSH.Server~~~~192.0.2.79" in script
    assert "autoMemoryReclaim" in script
    assert "pageReporting=true" in script


def test_wsl_memory_policy_is_bounded_and_promptly_reclaims_cache() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = [node for node in inventory["spec"]["nodes"] if node["platform"].startswith("windows")]
    for node in windows:
        assert node["wsl"]["memory"] == "12GB"
        assert node["wsl"]["swap"] == "3GB"
        assert node["wsl"]["autoMemoryReclaim"] == "dropCache"


def test_windows_management_uses_peer_wsl_jump_and_lan_destination() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = [node for node in inventory["spec"]["nodes"] if node["platform"].startswith("windows")]
    wsl_addresses = {node["wsl"]["nodeName"]: node["wsl"]["nodeAddress"] for node in windows}
    for node in windows:
        path = node["ssh"]["preferredPath"]
        peer_name = "restricted-node-296f36f2fb" if node["name"] == "a1-windows" else "restricted-node-1eb0570f74"
        assert path["mode"] == "peer-wsl-jump"
        assert path["jumpAddress"] == wsl_addresses[peer_name]
        assert path["jumpAddress"] != node["wsl"]["nodeAddress"]
        assert path["destinationAddress"] in node["ssh"]["addresses"]


def test_recovery_record_is_non_secret_and_reproducible() -> None:
    path = ROOT / "operations/host-ops/management-ssh-mesh/recovery-events/2026-09-06-a1-b1.json"
    record = json.loads(path.read_text())
    assert record["spec"]["openSshArtifact"]["hostKeysCopied"] is False
    assert len(record["spec"]["openSshArtifact"]["sshdSha256"]) == 64
    assert record["spec"]["verification"]["kubernetesReadyz"] == "ok"
    text = path.read_text().lower()
    assert "password" not in text
    assert "privatekey" not in text
