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


def test_host_recovery_is_role_bound_and_staggered() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    spec = inventory["spec"]
    windows = [node for node in spec["nodes"] if node["platform"].startswith("windows")]
    assert spec["watchdog"]["bootGraceSeconds"] >= 600
    assert spec["watchdog"]["nodeReplyTimeoutSeconds"] >= 300
    assert 10 <= spec["watchdog"]["localWslProbeTimeoutSeconds"] <= 60
    assert spec["watchdog"]["hostRebootCooldownSeconds"] >= 21600
    assert len({node["peerPriority"] for node in windows}) == len(windows)
    roles = {node["name"]: node["watchdog"] for node in windows}
    assert roles["a1-windows"] == {"role": "active-server", "serviceName": "k3s.service"}
    assert roles["b1-windows"] == {"role": "worker-standby", "serviceName": "k3s-agent.service"}


def test_no_secret_material_in_management_inventory() -> None:
    text = (ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text().lower()
    assert "privatekey" not in text
    assert '"password"' not in text
    assert "calvin" not in text


def test_windows_watchdog_uses_role_and_cooldown_guards() -> None:
    script = (ROOT / "operations/host-ops/windows/re8ch-control-plane-watchdog.ps1").read_text()
    assert "$heartbeat.role -eq $config.role" in script
    assert "hostRebootDelaySeconds" in script
    assert "hostRebootCooldownSeconds" in script
    assert "nodeReplyTimeoutSeconds" in script
    assert "control-plane-wsl-heartbeat.json" in script
    assert "$heartbeat.serviceReady -eq $true" in script
    assert "shutdown.exe" in script


def test_wsl_heartbeat_does_not_make_zerotier_a_host_reboot_gate() -> None:
    script = (
        ROOT
        / "operations/network/scripts/control-plane-watchdog/re8ch-control-plane-heartbeat.sh"
    ).read_text()
    assert 'systemctl is-active --quiet "$RE8CH_K3S_SERVICE"' in script
    assert "systemctl is-active --quiet ssh.service" in script
    assert '"serviceReady":true' in script
    assert '"zerotierReady":%s' in script
    assert 'grep -Fq "$RE8CH_NODE_IDENTITY/"' in script


def test_windows_installer_separates_wsl_owner_from_host_reboot_authority() -> None:
    script = (ROOT / "operations/host-ops/windows/install-re8ch-control-plane-host.ps1").read_text()
    assert "-UserId 'SYSTEM'" in script
    assert "-LogonType ServiceAccount" in script
    assert "OpenSSH.Server~~~~0.0.1.0" in script
    assert "autoMemoryReclaim" in script
    assert "pageReporting=true" in script


def test_wsl_memory_policy_is_bounded_and_gradually_reclaims_cache() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = [node for node in inventory["spec"]["nodes"] if node["platform"].startswith("windows")]
    for node in windows:
        assert node["wsl"]["memory"] == "12GB"
        assert node["wsl"]["swap"] == "3GB"
        assert node["wsl"]["autoMemoryReclaim"] == "gradual"


def test_windows_management_uses_peer_wsl_jump_and_lan_destination() -> None:
    inventory = json.loads((ROOT / "operations/host-ops/management-ssh-mesh/inventory.json").read_text())
    windows = [node for node in inventory["spec"]["nodes"] if node["platform"].startswith("windows")]
    wsl_addresses = {node["wsl"]["nodeName"]: node["wsl"]["nodeAddress"] for node in windows}
    for node in windows:
        path = node["ssh"]["preferredPath"]
        peer_name = "b1-wsl-zt" if node["name"] == "a1-windows" else "a1-wsl-zt"
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
