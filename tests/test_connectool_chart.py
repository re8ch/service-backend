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
    groups = [item for item in objects if item["kind"] == "MCPGroup"]
    assert [item["metadata"]["name"] for item in groups] == ["tenant-public"]
    oidc = next(item for item in objects if item["kind"] == "MCPOIDCConfig" and item["metadata"]["name"] == "re8ch-native-issuer")
    vmcp = next(item for item in objects if item["kind"] == "VirtualMCPServer" and item["metadata"]["name"] == "re8ch-native")
    dex = [item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "dex-static-clients"]
    codex = next(item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "codex-marketplace")
    registration = json.loads(codex["data"]["re8ch.mcp.json"])
    server = registration["mcpServers"]["re8ch"]
    assert oidc["spec"]["inline"]["issuer"] == "https://db-rw.re8ch.com/auth/v1"
    assert oidc["spec"]["inline"]["clientId"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["audience"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["resourceUrl"] == server["url"]
    assert vmcp["spec"]["replicas"] == 1
    assert vmcp["spec"]["sessionAffinity"] == "ClientIP"
    assert vmcp["spec"]["sessionStorage"] == {"provider": "memory"}
    assert dex == []


def test_skill_only_plugins_do_not_duplicate_mcp_registration():
    codex = next(item for item in render() if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "codex-marketplace")
    assert set(key for key in codex["data"] if key.endswith(".mcp.json")) == {"re8ch.mcp.json"}
    assert "re8ch-native" in codex["data"]["plugins.yaml"]


def test_cross_platform_distribution_contract_is_complete():
    contract = next(
        item for item in render()
        if item["kind"] == "ConfigMap"
        and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "platform-distribution"
    )
    payload = json.loads(contract["data"]["platforms.json"])
    by_id = {item["id"]: item for item in payload["channels"]}
    assert payload["updated"] == "2026-09-23"
    assert set(by_id) == {
        "openai-private", "openai-public", "anthropic-custom-connector",
        "anthropic-private-plugin", "anthropic-public", "cursor-team",
        "cursor-public", "workbuddy", "coze-custom", "coze-team-store", "doubao",
    }
    assert by_id["openai-private"]["entryClass"] == "self-service"
    assert by_id["openai-public"]["entryClass"] == "official-review"
    assert by_id["doubao"]["entryClass"] == "unavailable"
    assert by_id["doubao"]["status"] == "new-publication-unavailable"
    assert all(
        item.get("publication") == "re8ch-native"
        for item in payload["channels"]
        if item["entryClass"] != "unavailable"
    )


def test_chart_contains_no_credentials():
    rendered = yaml.safe_dump_all(render())
    for forbidden in ("clientSecret", "refreshToken", "privateKey", "kind: Secret"):
        assert forbidden not in rendered


def test_native_publication_has_exactly_one_mcp_owner():
    values = yaml.safe_load(VALUES.read_text())
    owners = [item for item in values["codex"]["plugins"] if item["mcpOwner"]]
    assert owners == [{"name": "re8ch", "publication": "re8ch-native", "category": "Productivity", "mcpOwner": True, "skills": []}]


def test_flux_oci_digest_suffix_is_sanitized_for_labels():
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    assert 'replace "+" "_"' in helpers


def test_native_resource_url_has_a_gateway_route():
    runtime = (ROOT / "clusters/re8ch/charts/birfucate-metering/templates/tenant-runtime.yaml").read_text()
    assert "metadata: {name: tenant-native-mcp, namespace: tenant-infrastructure}" in runtime
    assert "value: /tenant-native/mcp" in runtime
    assert "backendRefs: [{name: tenant-api, port: 80}]" in runtime


def test_connectool_renders_only_native_publication():
    objects = render()
    vmcps = {item["metadata"]["name"]: item for item in objects if item["kind"] == "VirtualMCPServer"}
    assert set(vmcps) == {"re8ch-native"}
    tenant = vmcps["re8ch-native"]
    assert tenant["spec"]["replicas"] == 1
    assert all(item["spec"]["sessionAffinity"] == "ClientIP" for item in vmcps.values())
    assert all(item["spec"]["sessionStorage"] == {"provider": "memory"} for item in vmcps.values())
    for item in vmcps.values():
        assert item["spec"]["podTemplateSpec"]["spec"]["nodeSelector"] == {
            "scheduling.re8ch.com/pool-service-core-r640": "true",
            "scheduling.re8ch.com/network-serving": "true",
        }
    assert tenant["spec"]["incomingAuth"]["oidcConfigRef"]["audience"] == "2981225a-da08-5aaa-9e84-8fd151696113"
    assert tenant["spec"]["incomingAuth"]["oidcConfigRef"]["resourceUrl"] == "https://tools.service.re8ch.com/tenant-native/mcp"


def test_connectool_renders_worker_runtime_and_disruption_contracts():
    objects = render()
    pdbs = {item["metadata"]["name"] for item in objects if item["kind"] == "PodDisruptionBudget"}
    assert pdbs == {"re8ch-native-session-router"}
    policies = [item for item in objects if item["kind"] == "MutatingAdmissionPolicy"]
    assert len(policies) == 2
    serialized = yaml.safe_dump_all(policies)
    assert "PreferSameNode" in serialized
    assert "progressDeadlineSeconds" in serialized
    assert "/spec/template/spec/nodeSelector/kubernetes.io~1hostname" in serialized
    assert "Object.spec.template.spec.affinity.nodeAffinity{" in serialized
    assert "Object.spec.template.spec.affinity.podAntiAffinity{" in serialized
    assert "'app': object.metadata.labels['app']" in serialized
    assert "r640" in serialized


def test_connectool_routes_memory_sessions_to_the_owning_vmcp_pod():
    objects = render()
    routers = {
        item["metadata"]["name"]: item
        for item in objects
        if item["kind"] == "Deployment"
        and item["metadata"]["labels"].get("app.kubernetes.io/component") == "session-router"
    }
    assert set(routers) == {"re8ch-native-session-router"}
    for name, deployment in routers.items():
        assert deployment["spec"]["replicas"] == 1
        spec = deployment["spec"]["template"]["spec"]
        assert spec["nodeSelector"] == {
            "scheduling.re8ch.com/pool-service-core-r640": "true",
            "scheduling.re8ch.com/network-serving": "true",
        }
        anti = spec["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
        assert anti["topologyKey"] == "kubernetes.io/hostname"
        env = {item["name"]: item for item in spec["containers"][0]["env"]}
        publication = name.removesuffix("-session-router")
        assert env["BACKEND_SERVICE"]["value"] == f"vmcp-{publication}"
        assert env["SESSION_HEADER"]["value"] == "Mcp-Session-Id"
        assert spec["containers"][0]["resources"]["limits"]["memory"] == "64Mi"

    services = {item["metadata"]["name"]: item for item in objects if item["kind"] == "Service"}
    assert all(services[name]["spec"]["trafficDistribution"] == "PreferSameNode" for name in routers)
    role = next(item for item in objects if item["kind"] == "Role" and item["metadata"]["name"] == "connectool-session-router")
    assert role["rules"] == [{"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"], "verbs": ["get", "list", "watch"]}]


def test_dex_reconciler_consumes_connectool_contract_and_retires_split_clients():
    objects = [item for item in yaml.safe_load_all((ROOT / "platform/connectool/dex-client-reconciler.yaml").read_text()) if item]
    deployment = next(item for item in objects if item["kind"] == "Deployment")
    script = next(item for item in objects if item["kind"] == "ConfigMap")["data"]["reconcile.py"]
    assert "connectool-dex-clients" in script
    assert "re8ch-tenant" not in script.replace('"re8ch-tenant-admin"', '').replace('"re8ch-tenant-registry"', '').replace('"re8ch-tenant-observability"', '').replace('"re8ch-tenant-byoc"', '')
    assert "re8ch-qwen-tenant" in script
    assert "re8ch-artchais-tenant" in script
    assert 'github_config.pop("orgs", None)' in script
    assert 'github_config["loadAllGroups"] = True' in script
    assert "config == original" in script
    assert "print(" not in script
    assert "@sha256:" in deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert deployment["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "r640"}
    assert not any(item["kind"] in {"Job", "CronJob"} for item in objects)


def test_dex_organization_claim_reconciliation_is_credential_preserving_and_idempotent():
    import base64
    import copy
    import logging
    import time
    import urllib
    import urllib.error

    objects = list(yaml.safe_load_all((ROOT / "platform/connectool/dex-client-reconciler.yaml").read_text()))
    script = next(item for item in objects if item["kind"] == "ConfigMap")["data"]["reconcile.py"]
    # Execute the real reconciliation against a fake API, without pod credentials.
    body = script[script.index("contract = None"):]
    config = {"connectors": [{"type": "github", "config": {
        "clientSecret": "test-only-secret", "org": "legacy",
        "orgs": [{"name": "Artchais", "teams": ["tenant-viewers"]}],
    }}], "staticClients": []}
    secret = {"data": {"config.yaml": base64.b64encode(yaml.safe_dump(config).encode()).decode()}}
    writes = []

    def request(path, method="GET", value=None, content_type="application/json"):
        if method == "GET" and path.endswith("connectool-dex-clients"):
            return {"data": {"clients.yaml": yaml.safe_dump({"staticClients": [{"id": "re8ch-tenant", "name": "Tenant"}]})}}
        if method == "GET" and path.endswith("grafana-dex-client"):
            return {"data": {"client.yaml": yaml.safe_dump({
                "id": "re8ch-grafana", "name": "RE8CH Grafana", "public": True,
                "redirectURIs": ["https://grafana.service.re8ch.com/grafana/login/generic_oauth"],
            })}}
        if method == "GET" and path.endswith("dex-config"):
            return copy.deepcopy(secret)
        writes.append((method, path))
        if method == "PATCH" and path.endswith("dex-config"):
            secret["data"].update(value["data"])
        return {}

    scope = dict(base64=base64, copy=copy, yaml=yaml, logging=logging, time=time, urllib=urllib, request=request)
    exec(compile(body, "dex-reconcile-test", "exec"), scope)
    updated = yaml.safe_load(base64.b64decode(secret["data"]["config.yaml"]))
    github = updated["connectors"][0]["config"]
    assert github["clientSecret"] == "test-only-secret"
    assert github["loadAllGroups"] is True
    assert "org" not in github and "orgs" not in github
    grafana = next(item for item in updated["staticClients"] if item["id"] == "re8ch-grafana")
    assert grafana["public"] is True
    assert grafana["redirectURIs"] == ["https://grafana.service.re8ch.com/grafana/login/generic_oauth"]
    assert any(path.endswith("deployments/dex") for _, path in writes)
    writes.clear()
    import pytest
    with pytest.raises(SystemExit) as stopped:
        exec(compile(body, "dex-reconcile-test", "exec"), scope)
    assert stopped.value.code == 0
    assert writes == []


def test_native_publication_delegates_authorization_to_tenant_backend():
    objects = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    publication = next(item for item in release["spec"]["values"]["toolhive"]["publications"] if item["name"] == "re8ch-native")
    policies = publication["authorization"]["policies"]
    assert policies == ['permit(principal, action, resource);']
