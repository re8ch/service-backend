from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "inventory/platform/controlled-subclusters.yaml"


def test_controlled_subclusters_are_credential_free_and_api_only():
    catalog = yaml.safe_load(CATALOG.read_text())
    defaults = catalog["spec"]["defaults"]
    assert defaults["relationship"] == "api-access-only"
    assert defaults["credentialPolicy"] == "oidc-no-static-credentials"
    assert defaults["dataPlaneSharing"] == "prohibited"

    forbidden = {"token", "clientCertificateData", "clientKeyData", "password"}
    for cluster in catalog["spec"]["clusters"]:
        assert cluster["organizationBoundary"] == "external"
        assert cluster["status"] in {"proposed", "active", "revoked"}
        assert forbidden.isdisjoint(cluster)
        assert (ROOT / cluster["ownerDeclaration"]).is_file()
        assert (ROOT / cluster["consumerDeclaration"]).is_file()


def test_owner_examples_are_not_reconciled_by_this_repository():
    owner_examples = ROOT / "platform/controlled-subclusters/owner-examples"
    references = []
    for path in ROOT.rglob("kustomization.yaml"):
        if ".git" not in path.parts:
            references.append(path.read_text())
    for manifest in owner_examples.glob("*.yaml"):
        assert all(manifest.name not in text for text in references)


def test_artc_owner_aliases_bind_one_explicit_admin():
    manifest = yaml.safe_load(
        (ROOT / "platform/controlled-subclusters/owner-examples/artc-headlamp-admin.yaml").read_text()
    )
    assert manifest["roleRef"]["name"] == "cluster-admin"
    assert {subject["name"] for subject in manifest["subjects"]} == {
        "oidc:trendstudiony@gmail.com",
        "oidc:artchais-hedyzhang",
    }
