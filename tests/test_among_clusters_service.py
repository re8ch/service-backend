from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIGEST = "sha256:7b7daf7e3e8ec0013b5223fa90e32b037c7b39ff4c6047fdb15b34018d34ce67"
CHART_DIGEST = "sha256:77fe286382c739eaf2b479387c5facda169403c3a636d4e4e3a0ceed81d00394"


def test_gitops_consumes_signed_oci_release_by_digest():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    source = next(item for item in objects if item["kind"] == "OCIRepository")
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/re8ch/charts/among-clusters-hub"
    assert source["spec"]["ref"]["digest"] == CHART_DIGEST
    assert release["spec"]["values"]["image"] == {
        "repository": "registry.re8ch.com/functions-shared/among-clusters",
        "digest": IMAGE_DIGEST,
    }
    assert release["spec"]["values"]["managedAccess"]["enabled"] is True
    assert release["spec"]["values"]["migration"]["replaceCollaborationCRDs"] is False
    assert release["spec"]["values"]["replicaCount"] == 1
    assert release["spec"]["values"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}


def test_gitops_keeps_human_relay_separate_from_service_health():
    release = (ROOT / "platform/among-clusters/release.yaml").read_text()
    assert IMAGE_DIGEST in release
    assert CHART_DIGEST in release
    assert "controlled-cluster-relay" not in release
    assert "among-clusters-hub-agent" not in release
    headlamp_resources = (ROOT / "platform/headlamp/kustomization.yaml").read_text()
    assert "among-clusters-catalog.yaml" not in headlamp_resources
    assert not (ROOT / "platform/headlamp/among-clusters-catalog.yaml").exists()


def test_major_service_upgrade_waits_without_rollback():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/release.yaml").read_text()))
    for release in (item for item in objects if item["kind"] == "HelmRelease"):
        assert release["spec"]["upgrade"]["timeout"] == "15m"
        assert release["spec"]["upgrade"]["remediation"] == {"retries": 0, "remediateLastFailure": False}
        assert "rollback" not in release["spec"]


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


def test_byoc_pilot_enables_generation_bound_managed_access_grants():
    objects = list(yaml.safe_load_all((ROOT / "platform/among-clusters/byoc-pilot.yaml").read_text()))
    by_kind = {}
    for item in objects:
        by_kind.setdefault(item["kind"], {})[item["metadata"]["name"]] = item
    for cluster in ("qwen", "artc"):
        peer = by_kind["Peer"][f"re8ch-{cluster}"]
        assert peer["spec"]["localIdentityRef"] == "re8ch"
        policy = by_kind["PeerPolicy"][f"{cluster}-kubernetes-api"]
        assert policy["spec"]["protocols"] == ["kubernetes-api"]
        grant = by_kind["ManagedAccessGrant"][f"{cluster}-headlamp-admin"]
        assert grant["spec"]["peerRef"] == f"re8ch-{cluster}"
        assert grant["spec"]["advertisementRef"] == f"{cluster}-default-kubernetes"
        assert grant["spec"]["scope"] == "Cluster"
        assert grant["spec"]["approved"] is True
