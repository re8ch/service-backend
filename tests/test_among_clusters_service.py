from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST = "sha256:98ecc17ca14b7ee9e640d9eaf6f97a21cedcffc0b17890bb3158fa48d488bff3"
CHART_DIGEST = "sha256:fb2c3e44d46552ceed87b69adf8314538a1f0b496ce7f90aa3d2059ad6a1f0fc"


def test_gitops_consumes_signed_oci_release_by_digest():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    source = next(item for item in objects if item["kind"] == "OCIRepository")
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/re8ch/charts/among-clusters-hub"
    assert source["spec"]["ref"]["digest"] == CHART_DIGEST
    assert release["spec"]["values"]["image"]["digest"] == IMAGE_DIGEST
    assert release["spec"]["values"]["managedAccess"]["enabled"] is False
    assert release["spec"]["values"]["migration"]["replaceCollaborationCRDs"] is False


def test_gitops_keeps_human_relay_separate_from_service_health():
    release = (ROOT / "platform/among-clusters/release.yaml").read_text()
    assert IMAGE_DIGEST in release
    assert CHART_DIGEST in release
    assert "controlled-cluster-relay" not in release
    assert "among-clusters-hub-agent" not in release
    headlamp_resources = (ROOT / "platform/headlamp/kustomization.yaml").read_text()
    assert "among-clusters-catalog.yaml" not in headlamp_resources
    assert not (ROOT / "platform/headlamp/among-clusters-catalog.yaml").exists()


def test_legacy_contracts_are_retained_for_review_but_not_reapplied():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/contracts.yaml").read_text()))
    assert any(item["kind"] == "CollaborationCluster" for item in objects)
    kustomization = yaml.safe_load((ROOT / "platform/among-clusters/kustomization.yaml").read_text())
    assert "contracts.yaml" not in kustomization["resources"]


def test_legacy_agent_cleanup_is_name_scoped():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/legacy-agent-cleanup.yaml").read_text()))
    role = next(item for item in objects if item["kind"] == "Role")
    job = next(item for item in objects if item["kind"] == "Job")
    assert role["rules"][0] == {
        "apiGroups": ["helm.toolkit.fluxcd.io"],
        "resources": ["helmreleases"],
        "resourceNames": ["among-clusters-hub-agent"],
        "verbs": ["get", "delete"],
    }
    assert role["rules"][1] == {
        "apiGroups": ["batch"],
        "resources": ["jobs"],
        "resourceNames": ["among-clusters-legacy-agent-cleanup"],
        "verbs": ["delete"],
    }
    assert "among-clusters-hub-agent" in job["spec"]["template"]["spec"]["containers"][0]["args"][0]
