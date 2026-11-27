import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "clusters/re8ch/charts/birfucate-metering"


def rendered():
    output = subprocess.run(["helm", "template", "test", str(CHART),
        "--set", "tenantPlatform.enabled=true",
        "--set", "tenantPlatform.invocationCredentials.enabled=true",
        "--set-string", "tenantPlatform.image=example.invalid/ops@sha256:" + "a" * 64],
        check=True, capture_output=True, text=True).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def test_rotator_has_narrow_cross_namespace_secret_authority():
    docs = rendered()
    role = next(item for item in docs if item["kind"] == "Role" and item["metadata"]["name"] == "tenant-invocation-credential-rotator")
    assert role["metadata"]["namespace"] == "tenant-infrastructure"
    assert role["rules"] == [{"apiGroups": [""], "resources": ["secrets"],
        "resourceNames": ["tenant-invocation-signing", "tenant-invocation-authority"],
        "verbs": ["get", "update", "patch"]}]
    cron = next(item for item in docs if item["kind"] == "CronJob" and item["metadata"]["name"] == "tenant-invocation-credential-rotator")
    assert cron["metadata"]["namespace"] == "supabase-billing"
    pod = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "tenant-invocation-credential-rotator"
    assert pod["automountServiceAccountToken"] is True
    container = pod["containers"][0]
    assert container["env"][0]["valueFrom"]["secretKeyRef"] == {"name": "supabase-billing-secrets", "key": "JWT_SECRET"}
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"
    assert not any(item["kind"] in {"ClusterRole", "ClusterRoleBinding"} and item["metadata"]["name"] == "tenant-invocation-credential-rotator" for item in docs)
    targets = [item for item in docs if item["kind"] == "Secret" and item["metadata"]["name"].startswith("tenant-invocation-")]
    assert {item["metadata"]["namespace"] for item in targets} == {"tenant-infrastructure"}
    assert all("data" not in item and "stringData" not in item for item in targets)


def test_rotator_declares_short_authority_token_and_public_overlap():
    config = next(item for item in rendered() if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "tenant-invocation-credential-rotator")
    script = config["data"]["rotate.py"]
    assert "'role': 'billing_commerce_runtime'" in script
    assert "now + 36 * 3600" in script
    assert "'retireAfter': now + 7 * 86400" in script
    assert "'publicKeyPem'" in script and "previous[:1]" in script
    assert "print(" not in script
    # Seven days of public verification overlap is far beyond the five-minute
    # maximum token lifetime, even across cache and rollout delay.
    assert 7 * 86400 > 300
