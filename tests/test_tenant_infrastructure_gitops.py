import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_contract_schemas_are_valid_json_and_have_stable_ids():
    instance = json.loads((ROOT / "schemas/tenant-service-instance.schema.json").read_text())
    usage = json.loads((ROOT / "schemas/usage-event.schema.json").read_text())
    assert instance["properties"]["kind"]["const"] == "TenantServiceInstance"
    assert usage["properties"]["schemaVersion"]["const"] == "usage.re8ch.com/v1"
    assert usage["additionalProperties"] is False


def test_tenant_runtime_has_dedicated_audience_no_kubernetes_token_and_digest_gate():
    docs = documents(ROOT / "platform/tenant-infrastructure/runtime.yaml")
    deployments = {item["metadata"]["name"]: item for item in docs if item["kind"] == "Deployment"}
    runtime_names = {"tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc"}
    assert runtime_names < deployments.keys()
    profiles = {}
    metadata_urls = {}
    for name in runtime_names:
        deployment = deployments[name]
        assert deployment["spec"]["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}
        pod = deployment["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert not any("persistentVolumeClaim" in volume for volume in pod.get("volumes", []))
        container = pod["containers"][0]
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        profiles[env["RE8CH_TOOL_PROFILE"]] = env["RE8CH_TOOL_AUDIENCE"]
        metadata_urls[env["RE8CH_TOOL_PROFILE"]] = env["PUBLIC_RESOURCE_METADATA_URL"]
        assert container["image"].startswith("registry.re8ch.com/image/re8ch-ai-operations@sha256:")
        assert "IMAGE_REQUIRES_VERIFIED_DIGEST" not in container["image"]
        assert container["imagePullPolicy"] == "IfNotPresent"
        assert container["startupProbe"]["httpGet"]["path"] == "/livez"
        assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
        assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert profiles == {
        "tenant-admin": "re8ch-tenant-admin",
        "tenant-registry": "re8ch-tenant-registry",
        "tenant-observability": "re8ch-tenant-observability",
        "tenant-byoc": "re8ch-tenant-byoc",
    }
    assert metadata_urls == {
        "tenant-admin": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant-admin/mcp",
        "tenant-registry": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/registry/mcp",
        "tenant-observability": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/observability/mcp",
        "tenant-byoc": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/byoc/mcp",
    }
    root_kustomization = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
    assert "platform/tenant-infrastructure" in root_kustomization


def test_tenancy_crds_are_structural_and_status_enabled():
    docs = documents(ROOT / "platform/tenant-infrastructure/tenancy-crds.yaml")
    assert {item["metadata"]["name"] for item in docs} == {
        "tenants.tenancy.re8ch.com",
        "tenantidentitybindings.tenancy.re8ch.com",
        "tenantgrants.tenancy.re8ch.com",
        "tenantserviceinstances.tenancy.re8ch.com",
    }
    for crd in docs:
        version = crd["spec"]["versions"][0]
        assert version["subresources"] == {"status": {}}
        assert version["schema"]["openAPIV3Schema"]["type"] == "object"
    identity = next(item for item in docs if item["metadata"]["name"].startswith("tenantidentitybindings."))
    validations = identity["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["x-kubernetes-validations"]
    rules = " ".join(item["rule"] for item in validations)
    assert "self.spec.issuer == oldSelf.spec.issuer" in rules
    assert "self.spec.subjectHash == oldSelf.spec.subjectHash" in rules
    schema = identity["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    assert schema["properties"]["role"]["enum"] == ["viewer", "operator", "admin"]
    assert "has(self.spec.subjectHash) != has(self.spec.group)" in rules


def test_artchais_github_team_bindings_have_explicit_roles():
    docs = documents(ROOT / "platform/tenant-infrastructure/artchais-tenant.yaml")
    tenant = next(item for item in docs if item["kind"] == "Tenant")
    bindings = [item for item in docs if item["kind"] == "TenantIdentityBinding"]
    assert tenant["metadata"]["name"] == "artchais"
    assert {(item["spec"]["group"], item["spec"]["role"]) for item in bindings} == {
        ("Artchais:tenant-admins", "admin"),
        ("Artchais:tenant-operators", "operator"),
        ("Artchais:tenant-viewers", "viewer"),
    }


def test_artchais_toolhive_publication_uses_signed_runtime_and_opaque_grants():
    runtime_docs = documents(ROOT / "platform/tenant-infrastructure/artchais-runtime.yaml")
    server = next(item for item in runtime_docs if item["kind"] == "MCPServer")
    env = {item["name"]: item["value"] for item in server["spec"]["env"]}
    assert server["spec"]["image"] == "registry.re8ch.com/image/re8ch-ai-operations@sha256:30bfefb7e126859b32326fa0557be161e2a27e7a426295eeae014468caa80593"
    assert env["RE8CH_TOOL_PROFILE"] == "tenant-artchais"
    assert env["RE8CH_TOOL_AUDIENCE"] == "re8ch-artchais-tenant"
    kubeconfigs = next(item for item in runtime_docs if item["kind"] == "ConfigMap")["data"]
    serialized = json.dumps(kubeconfigs)
    for role in ("viewer", "operator", "admin"):
        assert f"credential://among-clusters/artchais/artc-codex-{role}" in serialized
    assert "client-key-data" not in serialized

    release = documents(ROOT / "platform/connectool/artchais-release.yaml")[0]
    publication = release["spec"]["values"]["toolhive"]["publications"][0]
    assert publication["resourceUrl"] == "https://tools.re8ch.com/tenant/artchais/mcp"
    assert publication["clientId"] == "re8ch-artchais-tenant"
    policies = " ".join(publication["authorization"]["policies"])
    assert all(f"Artchais:tenant-{role}" in policies for role in ("admins", "operators", "viewers"))


def test_dex_refresh_rotation_has_bounded_reuse_window():
    script = (ROOT / "clusters/re8ch/components/ai-operations/register-dex-clients.sh").read_text()
    assert 'refresh["reuseInterval"] = "30s"' in script
    assert 'refresh.setdefault("validIfNotUsedFor", "720h")' in script
    assert 'refresh.setdefault("absoluteLifetime", "2160h")' in script
    assert 'refresh.pop("disableRotation", None)' in script


def test_plugin_definition_is_tenant_self_and_denies_financial_mutation():
    plugins = [yaml.safe_load((ROOT / "plugins" / name).read_text()) for name in ("tenant-infrastructure.yaml", "tenant-registry.yaml", "tenant-observability.yaml", "tenant-byoc.yaml")]
    assert {item["metadata"]["name"] for item in plugins} == {"tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc"}
    assert next(item for item in plugins if item["metadata"]["name"] == "tenant-admin")["spec"]["policy"]["resourceScopes"] == ["tenant-admin"]
    for plugin in plugins:
        denied = set(plugin["spec"]["policy"]["deniedFields"])
        assert {"credentialMaterial", "candidateRef", "price", "journalEntry"} <= denied
        assert plugin["spec"]["publication"]["enabled"] is False
