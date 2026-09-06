import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "catalogs/platform/modules/connectool/source/charts/connectool"
VALUES = ROOT / "platform/connectool/re8ch-values.yaml"


def render(*extra: str) -> list[dict]:
    command = ["helm", "template", "connectool", str(CHART), "--namespace", "connectool", "-f", str(VALUES), *extra]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_re8ch_chain_uses_one_identity_end_to_end():
    objects = render()
    assert not any(item["kind"] == "MCPGroup" for item in objects)
    oidc = next(item for item in objects if item["kind"] == "MCPOIDCConfig")
    vmcp = next(item for item in objects if item["kind"] == "VirtualMCPServer")
    dex = next(item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "dex-static-clients")
    codex = next(item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "codex-marketplace")
    registration = json.loads(codex["data"]["re8ch-cloud-platform-tool.mcp.json"])
    server = registration["mcpServers"]["re8ch-cloud-platform-tool"]
    assert oidc["spec"]["inline"]["issuer"] == "https://dex.re8ch.com"
    assert oidc["spec"]["inline"]["clientId"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["audience"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["resourceUrl"] == server["url"]
    assert "public: true" in dex["data"]["clients.yaml"]


def test_skill_only_plugins_do_not_duplicate_mcp_registration():
    codex = next(item for item in render() if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "codex-marketplace")
    assert "re8ch-cloud-platform-tool.mcp.json" in codex["data"]
    assert "re8ch-cluster-infra-tool.mcp.json" not in codex["data"]
    assert "re8ch-agent-orchestra-collab.mcp.json" not in codex["data"]
    assert "skills/cluster-infra/SKILL.md" in codex["data"]["plugins.yaml"]


def test_chart_contains_no_credentials():
    rendered = yaml.safe_dump_all(render())
    for forbidden in ("clientSecret", "refreshToken", "privateKey", "kind: Secret"):
        assert forbidden not in rendered


def test_duplicate_mcp_owners_are_rejected():
    result = subprocess.run(
        ["helm", "template", "connectool", str(CHART), "-f", str(VALUES), "--set", "codex.plugins[1].mcpOwner=true"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "exactly one Codex mcpOwner" in result.stderr


def test_flux_oci_digest_suffix_is_sanitized_for_labels():
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    assert 'replace "+" "_"' in helpers


def test_cloud_resource_url_has_a_gateway_route():
    route = yaml.safe_load((ROOT / "platform/connectool/cloud-route.yaml").read_text())
    paths = {rule["matches"][0]["path"]["value"] for rule in route["spec"]["rules"]}
    backends = {rule["backendRefs"][0]["name"] for rule in route["spec"]["rules"]}
    assert "/cloud" in paths
    assert "/.well-known/oauth-protected-resource/cloud" in paths
    assert backends == {"vmcp-re8ch-tools-vmcp"}


def test_artchais_dex_reconciler_is_one_shot_and_secret_safe():
    objects = [item for item in yaml.safe_load_all((ROOT / "platform/connectool/dex-artchais-reconciler.yaml").read_text()) if item]
    job = next(item for item in objects if item["kind"] == "Job")
    role = next(item for item in objects if item["kind"] == "Role")
    script = next(item for item in objects if item["kind"] == "ConfigMap")["data"]["register.sh"]
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert "@sha256:" in container["image"]
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    assert {rule["resources"][0] for rule in role["rules"]} == {"secrets", "deployments"}
    assert "re8ch-artchais-tenant" in script
    assert all(team in script for team in ("tenant-admins", "tenant-operators", "tenant-viewers"))
    assert "print(" not in script
