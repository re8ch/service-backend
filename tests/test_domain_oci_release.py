import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flux_oci", ROOT / "operations/releases/flux_oci.py")
flux_oci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flux_oci)


def test_workflow_uses_native_attestation_without_runtime_cosign():
    workflow = (ROOT / ".github/workflows/domain-oci-release.yml").read_text()
    assert "actions/attest@" in workflow
    assert "cosign-installer" not in workflow
    assert "cosign sign" not in workflow
    assert "needs: [publish, attest]" in workflow


def release():
    return {
        "apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "DomainRelease",
        "module": "platform/advanced-fabric", "codename": "bora",
        "bundle": {"url": "oci://ghcr.io/re8ch/advanced-fabric", "digest": "sha256:" + "a" * 64,
                   "signature": "github-attestation", "issuer": "https://token.actions.githubusercontent.com",
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


def test_native_attestation_is_recorded_without_unsupported_flux_verifier():
    source, _ = flux_oci.manifests(release())
    assert "verify" not in source["spec"]
    assert source["metadata"]["annotations"]["delivery.re8ch.com/attestation"] == "github-native"


def test_legacy_cosign_release_keeps_flux_identity_verification():
    data = release()
    data["bundle"]["signature"] = "cosign-keyless"
    source, _ = flux_oci.manifests(data)
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
