from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "clusters/re8ch/components/ops"


def test_control_plane_maintenance_is_bounded_and_never_prunes_runtime_state():
    script = (OPS / "control-plane-resource-maintenance").read_text()
    service = (OPS / "control-plane-resource-maintenance.service").read_text()
    timer = (OPS / "control-plane-resource-maintenance.timer").read_text()

    assert "journalctl --vacuum-size" in script
    assert "systemctl is-active --quiet k3s" in script
    assert "crictl" not in script
    assert "rm -" not in script
    assert "CPUQuota=5%" in service
    assert "MemoryMax=128M" in service
    assert "IOSchedulingClass=idle" in service
    assert "RandomizedDelaySec=45m" in timer
    assert "Persistent=true" in timer
