from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_rook_releases_preserve_existing_identity_and_are_suspended():
    releases = {
        item["metadata"]["name"]: item
        for item in documents(ROOT / "clusters/re8ch/components/rook-ceph/release.yaml")
        if item["kind"] == "HelmRelease"
    }
    assert set(releases) == {"rook-ceph", "ceph-csi-drivers", "rook-ceph-cluster"}
    for name, release in releases.items():
        assert release["spec"]["suspend"] is True
        assert release["spec"]["releaseName"] == name
        assert release["spec"]["targetNamespace"] == "rook-ceph"
        assert release["spec"]["storageNamespace"] == "rook-ceph"


def test_rook_external_charts_are_exactly_pinned():
    releases = [
        item for item in documents(ROOT / "clusters/re8ch/components/rook-ceph/release.yaml") if item["kind"] == "HelmRelease"
    ]
    versions = {item["metadata"]["name"]: item["spec"]["chart"]["spec"]["version"] for item in releases}
    assert versions == {"rook-ceph": "v1.20.3", "ceph-csi-drivers": "1.0.4", "rook-ceph-cluster": "v1.20.3"}


def test_harbor_adoption_keeps_custom_registry_and_excludes_staged_pvcs():
    kustomization = yaml.safe_load((ROOT / "clusters/re8ch/components/harbor/kustomization.yaml").read_text())
    assert "harbor-registry-app.yaml" in kustomization["resources"]
    assert "harbor-registry-oss-esa-image-patch.yaml" not in kustomization["resources"]
    assert {patch.get("path") for patch in kustomization["patches"] if isinstance(patch, dict)} >= {
        "harbor-registry-oss-esa-image-patch.yaml"
    }
    assert "harbor-cluster-storage-stage.yaml" not in kustomization["resources"]
