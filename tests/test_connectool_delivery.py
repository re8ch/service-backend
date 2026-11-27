from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_flux_consumes_pinned_signed_public_oci_chart():
    documents = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    source = next(item for item in documents if item["kind"] == "OCIRepository")
    release = next(item for item in documents if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/among-clusters/charts/connectool"
    assert source["spec"]["ref"] == {"tag": "0.6.2"}
    assert source["spec"]["verify"]["matchOIDCIdentity"] == [{
        "issuer": r"^https://token\.actions\.githubusercontent\.com$",
        "subject": r"^https://github\.com/among-clusters/connectool/\.github/workflows/release\.yaml@refs/tags/v0\.6\.2$",
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
    assert [item["name"] for item in publications] == ["re8ch-native"]
    native = publications[0]
    assert native["replicas"] == 1
    assert native["allowSingleReplicaWhenCapacityLimited"] is True
    assert native["resourceUrl"] == "https://tools.service.re8ch.com/tenant-native/mcp"
    assert native["workloads"] == [{"name": "tenant-api-backend"}]
    assert native["outgoingAuth"]["default"] == {
        "type": "externalAuthConfigRef",
        "externalAuthConfigRef": {"name": "tenant-vmcp-backend-auth"},
    }
    assert native["operational"]["listChanged"] == {"disabledWorkloads": ["tenant-api-backend"]}
    assert native["operational"]["failureHandling"]["circuitBreaker"]["enabled"] is False
    toolhive = release["spec"]["values"]["toolhive"]
    assert toolhive["sessionStorage"] == {"provider": "memory", "sessionAffinity": "ClientIP"}
    assert toolhive["sessionRouting"] == {
        "enabled": True,
        "replicas": 1,
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
    assert toolhive["runtimePolicy"]["nodeNames"] == ["r640"]
    assert toolhive["runtimePolicy"]["trafficDistribution"] == "PreferSameNode"
    assert toolhive["placement"]["hostAliases"] == [{
        "ip": "10.251.0.4",
        "hostnames": ["id.re8ch.com", "auth.re8ch.com"],
    }]
    plugins = release["spec"]["values"]["codex"]["plugins"]
    assert plugins == [{"name": "re8ch", "publication": "re8ch-native", "category": "Productivity", "mcpOwner": True, "skills": []}]


def test_standalone_platform_marketplace_is_retired_into_tenant_self_service():
    assert not (ROOT / "platform/agent-marketplace/plugins/marketplace.json").exists()
    assert not (ROOT / "platform/plugins/re8ch-platform-observe/.codex-plugin/plugin.json").exists()
    assert not (ROOT / "platform/plugins/re8ch-platform-operate/.codex-plugin/plugin.json").exists()
    registry = __import__("json").loads((ROOT / "platform/apps/tenant-mcp/platform-modules.json").read_text())
    assert not {"cloud", "infra", "admin"} & registry["backends"].keys()
    assert registry["backends"]["native"]["url"].endswith(":19443/mcp")
    assert all("allowed" not in backend for backend in registry["backends"].values())


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
        ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml",
        ROOT / "clusters/re8ch/charts/birfucate-metering/templates/tenant-runtime.yaml",
    ]
    rendered_routes = "\n".join(path.read_text() for path in route_files)
    assert "value: /tenant-native/mcp" in rendered_routes
    assert "backendRefs: [{name: tenant-api, port: 80}]" in rendered_routes
    for legacy in ("vmcp-re8ch-tools-vmcp", "vmcp-re8ch-admin-vmcp", "vmcp-tenant-vmcp"):
        assert legacy not in rendered_routes
    litellm = (ROOT / "clusters/re8ch/components/llm-gateway/litellm.yaml").read_text()
    assert "http://vmcp-re8ch-native.toolhive-system.svc.cluster.local:4483/mcp" in litellm
