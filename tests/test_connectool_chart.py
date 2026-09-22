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
    oidc = next(item for item in objects if item["kind"] == "MCPOIDCConfig" and item["metadata"]["name"] == "re8ch-tools-vmcp-issuer")
    vmcp = next(item for item in objects if item["kind"] == "VirtualMCPServer" and item["metadata"]["name"] == "re8ch-tools-vmcp")
    dex = next(item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "dex-static-clients")
    codex = next(item for item in objects if item["kind"] == "ConfigMap" and item["metadata"]["annotations"].get("ai.connectool.io/contract") == "codex-marketplace")
    registration = json.loads(codex["data"]["re8ch-cloud-platform-tool.mcp.json"])
    server = registration["mcpServers"]["re8ch-cloud-platform-tool"]
    assert oidc["spec"]["inline"]["issuer"] == "https://dex.service.re8ch.com"
    assert oidc["spec"]["inline"]["clientId"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["audience"] == server["oauth"]["clientId"]
    assert vmcp["spec"]["incomingAuth"]["oidcConfigRef"]["resourceUrl"] == server["url"]
    assert vmcp["spec"]["replicas"] == 1
    assert vmcp["spec"]["sessionAffinity"] == "ClientIP"
    assert vmcp["spec"]["sessionStorage"] == {"provider": "memory"}
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
    objects = yaml.safe_load_all((ROOT / "platform/connectool/cloud-route.yaml").read_text())
    route = next(item for item in objects if item and item["kind"] == "HTTPRoute")
    paths = {rule["matches"][0]["path"]["value"] for rule in route["spec"]["rules"]}
    backends = {rule["backendRefs"][0]["name"] for rule in route["spec"]["rules"]}
    assert "/cloud" in paths
    assert "/.well-known/oauth-protected-resource/cloud" in paths
    assert backends == {"re8ch-tools-vmcp-session-router"}


def test_connectool_renders_admin_and_tenant_publications():
    objects = render()
    vmcps = {item["metadata"]["name"]: item for item in objects if item["kind"] == "VirtualMCPServer"}
    assert {"re8ch-admin-vmcp", "tenant-vmcp"} <= vmcps.keys()
    tenant = vmcps["tenant-vmcp"]
    assert {name: item["spec"]["replicas"] for name, item in vmcps.items()} == {
        "re8ch-tools-vmcp": 1,
        "re8ch-admin-vmcp": 2,
        "tenant-vmcp": 2,
    }
    assert all(item["spec"]["sessionAffinity"] == "ClientIP" for item in vmcps.values())
    assert all(item["spec"]["sessionStorage"] == {"provider": "memory"} for item in vmcps.values())
    for item in vmcps.values():
        expressions = item["spec"]["podTemplateSpec"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"]
        assert expressions == [{
            "key": "kubernetes.io/hostname",
            "operator": "In",
            "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
        }]
    assert tenant["spec"]["incomingAuth"]["oidcConfigRef"]["audience"] == "re8ch-tenant"
    assert tenant["spec"]["incomingAuth"]["oidcConfigRef"]["resourceUrl"] == "https://tools.service.re8ch.com/tenant/mcp"


def test_connectool_renders_worker_runtime_and_disruption_contracts():
    objects = render()
    pdbs = {item["metadata"]["name"] for item in objects if item["kind"] == "PodDisruptionBudget"}
    assert pdbs == {
        "re8ch-admin-vmcp-pdb", "tenant-vmcp-pdb",
        "re8ch-tools-vmcp-session-router", "re8ch-admin-vmcp-session-router", "tenant-vmcp-session-router",
    }
    policies = [item for item in objects if item["kind"] == "MutatingAdmissionPolicy"]
    assert len(policies) == 2
    serialized = yaml.safe_dump_all(policies)
    assert "PreferSameNode" in serialized
    assert "progressDeadlineSeconds" in serialized
    assert "/spec/template/spec/nodeSelector/kubernetes.io~1hostname" in serialized
    assert "Object.spec.template.spec.affinity.nodeAffinity{" in serialized
    assert "Object.spec.template.spec.affinity.podAntiAffinity{" in serialized
    assert "'app': object.metadata.labels['app']" in serialized
    for node in ("restricted-node-1eb0570f74", "restricted-node-296f36f2fb"):
        assert node in serialized


def test_connectool_routes_memory_sessions_to_the_owning_vmcp_pod():
    objects = render()
    routers = {
        item["metadata"]["name"]: item
        for item in objects
        if item["kind"] == "Deployment"
        and item["metadata"]["labels"].get("app.kubernetes.io/component") == "session-router"
    }
    assert set(routers) == {
        "re8ch-tools-vmcp-session-router",
        "re8ch-admin-vmcp-session-router",
        "tenant-vmcp-session-router",
    }
    for name, deployment in routers.items():
        assert deployment["spec"]["replicas"] == 2
        spec = deployment["spec"]["template"]["spec"]
        terms = spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
        assert terms[0]["matchExpressions"][0] == {
            "key": "kubernetes.io/hostname",
            "operator": "In",
            "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
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
    assert deployment["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
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
        if method == "GET" and path.endswith("paralus-oidc-client"):
            return {"data": {"clientSecret": base64.b64encode(b"test-only-paralus-secret").decode()}}
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
    paralus = next(item for item in updated["staticClients"] if item["id"] == "paralus-service")
    assert paralus["secret"] == "test-only-paralus-secret"
    assert paralus["redirectURIs"] == [
        "https://paralus.service.re8ch.com/self-service/methods/oidc/callback/re8ch-dex"
    ]
    assert any(path.endswith("deployments/dex") for _, path in writes)
    writes.clear()
    import pytest
    with pytest.raises(SystemExit) as stopped:
        exec(compile(body, "dex-reconcile-test", "exec"), scope)
    assert stopped.value.code == 0
    assert writes == []


def test_cloud_publication_does_not_authorize_bare_external_organization():
    objects = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    release = next(item for item in objects if item["kind"] == "HelmRelease")
    publication = next(item for item in release["spec"]["values"]["toolhive"]["publications"] if item["name"] == "re8ch-tools-vmcp")
    policies = publication["authorization"]["policies"]
    assert 'permit(principal, action, resource);' not in policies
    assert all('THVGroup::"re8ch:' in policy for policy in policies)
