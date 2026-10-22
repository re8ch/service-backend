import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "operations/releases/helmrelease_inventory.py"
SPEC = importlib.util.spec_from_file_location("helmrelease_inventory", MODULE_PATH)
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


def report():
    return INVENTORY.build([ROOT / "clusters/re8ch", ROOT / "platform"])


def test_every_declared_helmrelease_has_an_explicit_classification():
    result = report()
    assert result["parseErrors"] == []
    assert result["releaseCount"] > 0
    assert result["classificationComplete"] is True
    assert result["missingClassifications"] == []
    assert result["staleClassifications"] == []
    assert all(item["classification"]["rollbackOwner"] for item in result["releases"])


def test_inventory_does_not_publish_inline_values_or_secret_values():
    result = report()
    serialized = INVENTORY.render(result)
    assert "inlineSha256" in serialized
    assert "inlinePresent" in serialized
    for release in result["releases"]:
        assert set(release["values"]) == {"inlinePresent", "inlineSha256", "references"}
        for reference in release["values"]["references"]:
            assert set(reference) == {"kind", "name", "valuesKey", "optional"}


def test_checked_inventory_matches_declarations_and_decisions():
    expected = INVENTORY.render(report())
    actual = (ROOT / "operations/releases/helmrelease-inventory.yaml").read_text()
    assert actual == expected


def test_high_blast_radius_releases_are_not_ssa_candidates():
    releases = {item["id"]: item for item in report()["releases"]}
    protected = {
        "helmrelease:flux-system:advanced-fabric",
        "helmrelease:flux-system:kyverno",
        "helmrelease:flux-system:paralus",
        "helmrelease:flux-system:rook-ceph",
        "helmrelease:flux-system:rook-ceph-cluster",
    }
    for identity in protected:
        assert releases[identity]["classification"]["disposition"] == "retain-helm-lifecycle"


def test_helmrelease_api_versions_are_an_explicit_allowlist():
    assert INVENTORY.HELM_RELEASE_API_VERSIONS == {
        "helm.toolkit.fluxcd.io/v2",
        "helm.toolkit.fluxcd.io/v2beta2",
        "helm.toolkit.fluxcd.io/v2beta1",
    }
