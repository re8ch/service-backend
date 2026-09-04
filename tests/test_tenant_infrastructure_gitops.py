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
    deployment = next(item for item in docs if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    profiles = {}
    for container in pod["containers"]:
        env = {item["name"]: item["value"] for item in container["env"]}
        profiles[env["RE8CH_TOOL_PROFILE"]] = env["RE8CH_TOOL_AUDIENCE"]
        assert container["image"].startswith("registry.re8ch.com/image/re8ch-ai-operations@sha256:")
        assert "IMAGE_REQUIRES_VERIFIED_DIGEST" not in container["image"]
    assert profiles == {
        "tenant-admin": "https://tools.re8ch.com/tenant-admin/mcp",
        "tenant-registry": "https://tools.re8ch.com/tenant/registry/mcp",
        "tenant-observability": "https://tools.re8ch.com/tenant/observability/mcp",
        "tenant-byoc": "https://tools.re8ch.com/tenant/byoc/mcp",
    }
    root_kustomization = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
    assert "platform/tenant-infrastructure" in root_kustomization


def test_plugin_definition_is_tenant_self_and_denies_financial_mutation():
    plugins = [yaml.safe_load((ROOT / "plugins" / name).read_text()) for name in ("tenant-infrastructure.yaml", "tenant-registry.yaml", "tenant-observability.yaml", "tenant-byoc.yaml")]
    assert {item["metadata"]["name"] for item in plugins} == {"tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc"}
    assert next(item for item in plugins if item["metadata"]["name"] == "tenant-admin")["spec"]["policy"]["resourceScopes"] == ["tenant-admin"]
    for plugin in plugins:
        denied = set(plugin["spec"]["policy"]["deniedFields"])
        assert {"credentialMaterial", "candidateRef", "price", "journalEntry"} <= denied
        assert plugin["spec"]["publication"]["enabled"] is False
