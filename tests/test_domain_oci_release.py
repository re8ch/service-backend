import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flux_oci", ROOT / "operations/releases/flux_oci.py")
flux_oci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flux_oci)


def release():
    return {
        "apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "DomainRelease",
        "module": "platform/advanced-fabric", "codename": "bora",
        "bundle": {"url": "oci://ghcr.io/re8ch/advanced-fabric", "digest": "sha256:" + "a" * 64,
                   "signature": "cosign-keyless", "issuer": "https://token.actions.githubusercontent.com",
                   "identity": "https://github.com/re8ch/advanced-fabric/.github/workflows/release.yml@refs/heads/main"},
        "images": [{"name": "ghcr.io/re8ch/advanced-fabric", "tag": "bora", "digest": "sha256:" + "b" * 64}],
    }


@pytest.fixture(autouse=True)
def reviewed_publisher(monkeypatch):
    data = release()
    monkeypatch.setattr(flux_oci, "approved_publishers", lambda: {data["module"]: {
        "identity": data["bundle"]["identity"], "issuer": data["bundle"]["issuer"]}})


def test_source_is_digest_pinned_and_only_target_module_reconciles():
    source, reconciliation = flux_oci.manifests(release())
    assert source["spec"]["ref"] == {"digest": "sha256:" + "a" * 64}
    assert reconciliation["spec"]["sourceRef"]["name"] == source["metadata"]["name"]
    assert reconciliation["metadata"]["annotations"]["delivery.re8ch.com/module"] == "platform/advanced-fabric"
    assert reconciliation["spec"]["prune"] is False


def test_signer_is_anchored_not_wildcard():
    source, _ = flux_oci.manifests(release())
    identity = source["spec"]["verify"]["matchOIDCIdentity"][0]
    assert identity["subject"].startswith("^") and identity["subject"].endswith("$")


@pytest.mark.parametrize("field,value", [("module", "platform/unreviewed"), ("codename", "42")])
def test_unreviewed_module_and_numeric_codename_rejected(field, value):
    data = release()
    data[field] = value
    with pytest.raises(Exception):
        flux_oci.manifests(data)


def test_mutable_or_numeric_image_tag_rejected():
    data = copy.deepcopy(release())
    data["images"][0]["tag"] = "latest"
    with pytest.raises(ValueError, match="codename"):
        flux_oci.manifests(data)


def test_different_org_workflow_is_not_an_approved_module_publisher():
    data = release()
    data["bundle"]["identity"] = "https://github.com/re8ch/unreviewed/.github/workflows/release.yml@refs/heads/main"
    with pytest.raises(ValueError, match="independently reviewed"):
        flux_oci.manifests(data)
