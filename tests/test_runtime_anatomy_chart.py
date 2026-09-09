import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
CHART = ROOT / "clusters/re8ch/charts/runtime-anatomy"


def render(*args):
    result = subprocess.run(
        ["helm", "template", "runtime-anatomy", str(CHART), "--namespace", "runtime-anatomy", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_default_chart_is_configuration_only_and_has_final_scope():
    subprocess.run(["helm", "lint", str(CHART)], check=True, capture_output=True, text=True)
    resources = render()
    assert [item["kind"] for item in resources] == ["ConfigMap"]

    scope = yaml.safe_load(resources[0]["data"]["scope.yaml"])
    spec = scope["spec"]
    assert scope["kind"] == "RuntimeScope"
    assert spec["lifecycle"] == "Canary"
    assert spec["retention"]["gcMode"] == "DryRun"
    assert spec["credentials"]["captureValues"] is False
    assert spec["images"]["policy"]["requireDigest"] is True
    assert all("@sha256:" in image["reference"] for image in spec["images"]["catalog"])

    backends = {item["name"]: item for item in spec["storageBackends"]}
    assert backends["replicated-files"]["storageClassName"] == "cephfs-re8ch"
    assert backends["r640-files"]["durability"] == "NodeLocal"
    assert backends["object-artifacts"]["type"] == "S3Compatible"
    assert backends["node-cache"]["durability"] == "Ephemeral"


def test_canary_claim_uses_state_class_and_retention_guards():
    resources = render(
        "--set",
        "provisioning.enabled=true",
        "--set",
        "provisioning.claims[0].name=runtime-anatomy-canary",
        "--set",
        "provisioning.claims[0].stateClassRef=workspace",
        "--set",
        "provisioning.claims[0].size=1Gi",
    )
    claim = next(item for item in resources if item["kind"] == "PersistentVolumeClaim")
    assert claim["spec"]["storageClassName"] == "cephfs-re8ch"
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert claim["metadata"]["labels"]["storage.re8ch.com/replicated-approved"] == "true"


def test_flux_release_keeps_provisioning_and_gc_destructive_actions_disabled():
    release = yaml.safe_load((ROOT / "platform/runtime-anatomy/release.yaml").read_text())
    assert release["spec"]["chart"]["spec"]["chart"] == "./clusters/re8ch/charts/runtime-anatomy"
    assert release["spec"]["values"]["provisioning"]["enabled"] is False
    assert release["spec"]["values"]["retention"]["gcMode"] == "DryRun"

    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../../platform/runtime-anatomy" in root["resources"]
