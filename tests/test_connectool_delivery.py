from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_flux_consumes_pinned_signed_public_oci_chart():
    documents = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    source = next(item for item in documents if item["kind"] == "OCIRepository")
    release = next(item for item in documents if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/among-clusters/charts/connectool"
    assert source["spec"]["ref"] == {"tag": "0.6.1"}
    assert source["spec"]["verify"]["matchOIDCIdentity"] == [{
        "issuer": r"^https://token\.actions\.githubusercontent\.com$",
        "subject": r"^https://github\.com/among-clusters/connectool/\.github/workflows/release\.yaml@refs/tags/v0\.6\.1$",
    }]
    assert source["spec"]["verify"]["provider"] == "cosign"
    assert source["spec"]["verify"]["matchOIDCIdentity"]
    assert release["spec"]["chartRef"] == {"kind": "OCIRepository", "name": "connectool"}
    distribution = release["spec"]["values"]["distribution"]
    assert distribution["updated"] == "2026-09-23"
    assert {item["entryClass"] for item in distribution["channels"]} == {
        "self-service", "official-review", "unavailable",
    }
    assert len(distribution["channels"]) == 11
    registry = release["spec"]["values"]["toolhive"]["registry"]["release"]
    assert registry["enabled"] is True
    assert registry["version"] == "1.5.0"
    registry_expressions = registry["values"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"]
    assert registry_expressions == [
        {"key": "scheduling.re8ch.com/pool-service-core-r640", "operator": "In", "values": ["true"]},
        {"key": "scheduling.re8ch.com/network-serving", "operator": "In", "values": ["true"]},
    ]
    assert registry["values"]["replicaCount"] == 1
    assert registry["values"]["tolerations"] == [
        {"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"},
        {
            "key": "scheduling.re8ch.com/capacity-window",
            "operator": "Equal",
            "value": "closed",
            "effect": "NoSchedule",
        },
    ]
    publications = release["spec"]["values"]["toolhive"]["publications"]
    assert {item["name"] for item in publications} == {"re8ch-tools-vmcp", "re8ch-admin-vmcp", "tenant-vmcp"}
    assert {item["name"]: item["replicas"] for item in publications} == {
        "re8ch-tools-vmcp": 1,
        "re8ch-admin-vmcp": 1,
        "tenant-vmcp": 1,
    }
    assert all(item.get("allowSingleReplicaWhenCapacityLimited") is True for item in publications)
    by_name = {item["name"]: item for item in publications}
    assert by_name["re8ch-tools-vmcp"]["allowSingleReplicaWhenCapacityLimited"] is True
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
        "externalAuthConfigRef": {"name": "tenant-vmcp-backend-auth"},
    }
    assert by_name["tenant-vmcp"]["operational"]["listChanged"] == {
        "disabledWorkloads": ["tenant-api-backend"],
    }
    assert "listChanged" not in by_name["re8ch-tools-vmcp"]["operational"]
    assert "listChanged" not in by_name["re8ch-admin-vmcp"]["operational"]
    assert all(
        item["operational"]["failureHandling"]["partialFailureMode"] == "best_effort"
        for item in publications
    )
    toolhive = release["spec"]["values"]["toolhive"]
    assert toolhive["sessionStorage"] == {"provider": "memory", "sessionAffinity": "ClientIP"}
    assert toolhive["sessionRouting"] == {
        "enabled": True,
        "replicas": 2,
        "image": {
            "repository": "registry.re8ch.com/functions-shared/connectool-session-router",
            "tag": "0.6.0-amd64",
            "digest": "sha256:df1a3def6fcf05916e5fce18d5147e3ff5e1804780060b9da54de7c571e5359a",
            "pullPolicy": "IfNotPresent",
        },
        "listenPort": 8080,
        "servicePort": 4483,
        "backendPort": 4483,
        "sessionHeader": "Mcp-Session-Id",
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"cpu": "100m", "memory": "64Mi"},
        },
    }
    assert toolhive["runtimePolicy"]["nodeNames"] == ["restricted-node-6dc3f140f9"]
    assert toolhive["runtimePolicy"]["trafficDistribution"] == "PreferSameNode"
    assert toolhive["placement"]["hostAliases"] == [{
        "ip": "192.0.2.209",
        "hostnames": ["id-stalwart.tools.re8ch.com", "auth.re8ch.com"],
    }]
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


def test_public_and_internal_mcp_clients_use_declared_frontends():
    route_files = [
        ROOT / "platform/connectool/cloud-route.yaml",
        ROOT / "clusters/re8ch/components/regional-routing/migrated-public-http-routes.yaml",
        ROOT / "clusters/re8ch/components/toolhive/external-observe-publication.yaml",
        ROOT / "clusters/re8ch/components/toolhive/external-privileged-publication.yaml",
        ROOT / "clusters/re8ch/charts/birfucate-metering/templates/tenant-runtime.yaml",
    ]
    rendered_routes = "\n".join(path.read_text() for path in route_files)
    # The privileged Tools publication is a singleton on restricted-node-6dc3f140f9 during the
    # A1/B1 fabric incident, so its public route can safely target vMCP
    # directly. The singleton tenant publication also bypasses its router;
    # only the multi-replica admin publication requires session routing here.
    assert "vmcp-re8ch-tools-vmcp" in rendered_routes
    assert "vmcp-re8ch-admin-vmcp" not in rendered_routes
    assert "re8ch-tools-vmcp-session-router" in rendered_routes
    assert "re8ch-admin-vmcp-session-router" in rendered_routes
    assert "backendRefs: [{name: vmcp-tenant-vmcp, port: 4483}]" in rendered_routes
    toolhive_ingresses = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "clusters/re8ch/components/toolhive/external-observe-publication.yaml",
            "clusters/re8ch/components/toolhive/external-privileged-publication.yaml",
        )
    )
    assert "ingressClassName: traefik" not in toolhive_ingresses
    litellm = (ROOT / "clusters/re8ch/components/llm-gateway/litellm.yaml").read_text()
    assert "http://re8ch-tools-vmcp-session-router.toolhive-system.svc.cluster.local:4483/mcp" in litellm
