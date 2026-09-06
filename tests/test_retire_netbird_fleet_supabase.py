from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OBJECTS = [item for item in yaml.safe_load_all((ROOT / "platform/retire-netbird-fleet-supabase/retirement.yaml").read_text()) if item]


def script():
    job = next(item for item in OBJECTS if item["kind"] == "Job")
    return job["spec"]["template"]["spec"]["containers"][0]["args"][0]


def test_public_routes_are_withdrawn_before_scale_down():
    commands = script()
    assert commands.index("delete ingress/netbird-access-dns") < commands.index("scale deployment/netbird-dashboard")
    assert commands.index("delete ingress/fleet") < commands.index("scale deployment/fleet")
    assert commands.index("delete ingress/supabase-pg-re8ch-studio") < commands.index("scale deployment/supabase-auth")


def test_retirement_preserves_data_and_excludes_other_products():
    commands = script().lower()
    assert "delete pvc" not in commands
    assert "delete pv" not in commands
    assert "delete secret" not in commands
    for excluded in ("qwen", "qianwen-ops", "ledger"):
        assert excluded not in commands


def test_all_mutating_rbac_is_resource_name_bounded():
    role = next(item for item in OBJECTS if item["kind"] == "ClusterRole")
    assert all(rule.get("resourceNames") for rule in role["rules"])
    assert all("*" not in rule.get("verbs", []) for rule in role["rules"])


def test_job_image_and_security_are_hardened():
    job = next(item for item in OBJECTS if item["kind"] == "Job")
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert "@sha256:" in container["image"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
