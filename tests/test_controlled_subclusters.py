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


def test_gitops_ssh_config_is_only_a_sops_secret():
    manifests = yaml.safe_load_all(
        (ROOT / "clusters/re8ch/secrets/ai-operations-inventory.sops.yaml").read_text()
    )
    secret = next(
        item
        for item in manifests
        if item
        and item.get("kind") == "Secret"
        and item.get("metadata", {}).get("name") == "ai-operations-ssh-config"
    )
    assert secret["stringData"]["ssh-config"].startswith("ENC[")
    assert "sops" in secret
    assert not (ROOT / "clusters/re8ch/components/ai-operations/ssh_config.template").exists()


def test_plaintext_operations_targets_are_absent():
    assert not (ROOT / "inventory/nodes.yaml").exists()
    assert not (ROOT / "contracts/infrastructure/targets.yaml").exists()


def test_cloud_platform_mounts_gitops_ssh_config_and_secret_identity():
    documents = yaml.safe_load_all(
        (ROOT / "platform/cloud-platform/runtime.yaml").read_text()
    )
    deployment = next(item for item in documents if item and item.get("kind") == "Deployment")
    ssh = next(volume for volume in deployment["spec"]["template"]["spec"]["volumes"] if volume["name"] == "ssh-projected")
    sources = ssh["projected"]["sources"]
    assert sources[0]["secret"] == {
        "name": "ai-operations-ssh-config",
        "items": [{"key": "ssh-config", "path": "ssh-config"}],
    }
    assert sources[1]["secret"] == {
        "name": "cloud-platform-runtime",
        "items": [{"key": "id_ed25519", "path": "id_ed25519"}],
    }
