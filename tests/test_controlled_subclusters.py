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


def test_gitops_ssh_config_matches_reviewable_template():
    manifests = yaml.safe_load_all(
        (ROOT / "clusters/re8ch/components/ai-operations/platform.yaml").read_text()
    )
    config = next(
        item
        for item in manifests
        if item
        and item.get("kind") == "ConfigMap"
        and item.get("metadata", {}).get("name") == "ai-operations-ssh-config"
    )
    template = (
        ROOT / "clusters/re8ch/components/ai-operations/ssh_config.template"
    ).read_text()
    assert config["data"]["ssh-config"] == template


def test_windows_hosts_use_cross_host_wsl_recovery_paths():
    template = (
        ROOT / "clusters/re8ch/components/ai-operations/ssh_config.template"
    ).read_text()
    assert "Host a1-windows\n  HostName 192.0.2.246\n  User Administrator\n  ProxyJump restricted-node-296f36f2fb" in template
    assert "Host b1-windows\n  HostName 192.0.2.35\n  User Administrator\n  ProxyJump restricted-node-1eb0570f74" in template

    targets = yaml.safe_load((ROOT / "inventory/nodes.yaml").read_text())["items"]
    by_name = {item["metadata"]["name"]: item["spec"] for item in targets}
    for name in ("a1-windows", "b1-windows"):
        assert by_name[name]["address"] == name
        assert by_name[name]["credentialRef"] == "node-ssh"
        assert "wsl-lifecycle" in by_name[name]["capabilities"]
