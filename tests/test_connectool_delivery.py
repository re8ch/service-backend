from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_flux_consumes_pinned_signed_public_oci_chart():
    documents = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    source = next(item for item in documents if item["kind"] == "OCIRepository")
    release = next(item for item in documents if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/among-clusters/charts/connectool"
    assert source["spec"]["ref"] == {"tag": "0.4.1"}
    assert source["spec"]["verify"]["provider"] == "cosign"
    assert source["spec"]["verify"]["matchOIDCIdentity"]
    assert release["spec"]["chartRef"] == {"kind": "OCIRepository", "name": "connectool"}
    registry = release["spec"]["values"]["toolhive"]["registry"]["release"]
    assert registry["enabled"] is True
    assert registry["version"] == "1.5.0"
    publications = release["spec"]["values"]["toolhive"]["publications"]
    assert {item["name"] for item in publications} == {"re8ch-tools-vmcp", "re8ch-admin-vmcp", "tenant-vmcp"}
    assert {item["replicas"] for item in publications} == {2}
    by_name = {item["name"]: item for item in publications}
    assert by_name["re8ch-tools-vmcp"]["workloads"] == [
        {"name": "ops-observe-vmcp-internal", "toolConfigRef": "ops-public-read-tools"},
        {"name": "ops-operate-vmcp-internal", "toolConfigRef": "ops-operate-tools"},
    ]
    assert by_name["re8ch-admin-vmcp"]["workloads"] == [
        {"name": "ops-admin-vmcp-internal", "toolConfigRef": "ops-admin-tools"},
        {"name": "admin-api-backend"},
    ]
    assert by_name["re8ch-tools-vmcp"]["outgoingAuth"]["default"] == {
        "type": "externalAuthConfigRef",
        "externalAuthConfigRef": {"name": "vmcp-observe-backend-auth"},
    }
    assert by_name["re8ch-tools-vmcp"]["outgoingAuth"]["backends"] == {
        "ops-observe-vmcp-internal": {
            "type": "externalAuthConfigRef",
            "externalAuthConfigRef": {"name": "vmcp-observe-backend-auth"},
        },
        "ops-operate-vmcp-internal": {
            "type": "externalAuthConfigRef",
            "externalAuthConfigRef": {"name": "vmcp-admin-backend-auth"},
        },
    }
    assert by_name["re8ch-admin-vmcp"]["outgoingAuth"]["default"] == {
        "type": "externalAuthConfigRef",
        "externalAuthConfigRef": {"name": "vmcp-admin-backend-auth"},
    }
    assert by_name["re8ch-admin-vmcp"]["outgoingAuth"]["backends"]["admin-api-backend"] == {
        "type": "externalAuthConfigRef",
        "externalAuthConfigRef": {"name": "admin-vmcp-backend-auth"},
    }
    assert by_name["tenant-vmcp"]["outgoingAuth"]["default"] == {
        "type": "externalAuthConfigRef",
        "externalAuthConfigRef": {"name": "admin-vmcp-backend-auth"},
    }
    assert all(
        item["operational"]["failureHandling"]["partialFailureMode"] == "best_effort"
        for item in publications
    )
    toolhive = release["spec"]["values"]["toolhive"]
    assert toolhive["sessionStorage"] == {"provider": "memory", "sessionAffinity": "ClientIP"}
    assert toolhive["runtimePolicy"]["nodeNames"] == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
    assert toolhive["runtimePolicy"]["trafficDistribution"] == "PreferSameNode"
    plugins = release["spec"]["values"]["codex"]["plugins"]
    tenant_admin = next(item for item in plugins if item["name"] == "re8ch-tenant-admin")
    assert tenant_admin["publication"] == "re8ch-admin-vmcp"
    assert tenant_admin["mcpOwner"] is False


def test_artifact_hub_uses_registered_repository_id():
    upstream = ROOT / "catalogs/platform/modules/connectool/source"
    metadata = yaml.safe_load((upstream / "charts/connectool/artifacthub-repo.yml").read_text())
    assert metadata["repositoryID"] == "1bbd015f-2e46-494f-a2e2-a03cabbb5d31"
    assert metadata["owners"]


def test_release_is_digest_signed_and_verified():
    release = (ROOT / "catalogs/platform/modules/connectool/source/.github/workflows/release.yaml").read_text()
    for expected in ("id-token: write", "cosign sign", "cosign verify", "token.actions.githubusercontent.com"):
        assert expected in release
