from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST = "sha256:76c9d574939feff136205d2f9176b14f483c81735f24af31c68bae46d15ff4f3"
CHART_DIGEST = "sha256:39dab95cd9658e9539a823155be7ad80f21204d43176a45585c524942fbcd564"


def test_gitops_consumes_signed_oci_release_by_digest():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    source = next(item for item in objects if item["kind"] == "OCIRepository")
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/re8ch/charts/among-clusters-hub"
    assert source["spec"]["ref"]["digest"] == CHART_DIGEST
    assert release["spec"]["values"]["image"]["digest"] == IMAGE_DIGEST
    assert release["spec"]["values"]["managedAccess"]["enabled"] is False
    assert release["spec"]["values"]["migration"]["replaceCollaborationCRDs"] is True


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
