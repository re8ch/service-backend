import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def tenant_chart_documents():
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    image = "registry.re8ch.com/image/re8ch-ai-operations@sha256:443d9e822230ab0daf59f6c6344caa69843c45d0bb9e78330f34f1d9e3305c6d"
    rendered = subprocess.run(
        ["helm", "template", "birfucate", str(chart), "--namespace", "observability-ai", "--kube-version", "1.35.0", "--include-crds", "--set", "tenantPlatform.enabled=true", "--set-string", f"tenantPlatform.image={image}"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(rendered) if item]


def test_contract_schemas_are_valid_json_and_have_stable_ids():
    instance = json.loads((ROOT / "schemas/tenant-service-instance.schema.json").read_text())
    usage = json.loads((ROOT / "schemas/usage-event.schema.json").read_text())
    assert instance["properties"]["kind"]["const"] == "TenantServiceInstance"
    assert usage["properties"]["schemaVersion"]["const"] == "usage.re8ch.com/v1"
    assert usage["additionalProperties"] is False


def test_tenant_runtime_has_only_admin_and_tenant_audiences_and_digest_gate():
    docs = tenant_chart_documents()
    runtime_names = {"admin-api", "tenant-api"}
    deployments = {item["metadata"]["name"]: item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] in runtime_names}
    assert runtime_names == deployments.keys()
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
        assert container["image"] == "registry.re8ch.com/image/re8ch-ai-operations@sha256:443d9e822230ab0daf59f6c6344caa69843c45d0bb9e78330f34f1d9e3305c6d"
        assert container["imagePullPolicy"] == "IfNotPresent"
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        assert env["DEVICE_REGISTRY_PATH"] == "/tmp/devices.db"
        assert container["startupProbe"]["httpGet"]["path"] == "/livez"
        assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
        assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert profiles == {"admin": "re8ch-platform-admin", "tenant": "re8ch-tenant"}
    assert metadata_urls == {
        "admin": "https://admin.re8ch.com:8443/.well-known/oauth-protected-resource/mcp",
        "tenant": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/mcp",
    }


def test_production_tenant_readiness_degrades_explicitly_while_harbor_worker_is_absent():
    release = documents(ROOT / "platform/birfucate-metering/release.yaml")[0]
    assert release["spec"]["values"]["tenantPlatform"]["tenantReadinessPath"] == "/livez"


def test_tenant_namespace_outlives_helm_remediation_and_protects_sops_secrets():
    root_namespaces = documents(ROOT / "clusters/re8ch/namespaces.yaml")
    namespace = next(item for item in root_namespaces if item["metadata"]["name"] == "tenant-infrastructure")
    assert namespace["metadata"]["labels"]["saas.re8ch.com/tenant"] == "platform"
    chart_docs = tenant_chart_documents()
    assert not any(item["kind"] == "Namespace" for item in chart_docs)
    claims = {item["metadata"]["name"] for item in chart_docs if item["kind"] == "PersistentVolumeClaim"}
    assert "tenant-infrastructure-state" not in claims


def test_tenancy_crds_are_structural_and_status_enabled():
    docs = tenant_chart_documents()
    docs = [item for item in docs if item["kind"] == "CustomResourceDefinition" and item["spec"]["group"] == "tenancy.re8ch.com"]
    assert {item["metadata"]["name"] for item in docs} == {
        "tenants.tenancy.re8ch.com",
        "tenantidentitybindings.tenancy.re8ch.com",
        "tenantgrants.tenancy.re8ch.com",
        "tenantserviceinstances.tenancy.re8ch.com",
        "tenantresourceclaims.tenancy.re8ch.com",
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
    docs = tenant_chart_documents()
    tenant = next(item for item in docs if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.re8ch.com/"))
    bindings = [item for item in docs if item["kind"] == "TenantIdentityBinding"]
    assert tenant["metadata"]["name"] == "artchais"
    assert tenant["spec"]["runtimeNamespaceRef"] == "artchais"
    assert tenant["spec"]["imagePullSecretRef"] == "harbor-functions-shared-pull"
    assert {(item["spec"]["group"], item["spec"]["role"]) for item in bindings} == {
        ("Artchais:tenant-admins", "admin"),
        ("Artchais:tenant-operators", "operator"),
        ("Artchais:tenant-viewers", "viewer"),
    }
    grants = [item for item in docs if item["kind"] == "TenantGrant"]
    assert {item["spec"]["consumableRef"] for item in grants} == {"compute.workload.v1", "storage.volume.v1"}


def test_connectool_has_one_generic_tenant_publication():
    release = documents(ROOT / "platform/connectool/release.yaml")[1]
    publications = release["spec"]["values"]["toolhive"]["publications"]
    tenant = next(item for item in publications if item["name"] == "tenant-vmcp")
    assert tenant["clientId"] == "re8ch-tenant"
    assert tenant["resourceUrl"] == "https://tools.re8ch.com/tenant/mcp"
    assert tenant["workloads"] == [{"name": "tenant-api-backend"}]
    serialized = json.dumps(publications)
    for legacy in ("tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc", "tenant-artchais"):
        assert legacy not in serialized


def test_tenant_public_route_preserves_end_user_identity_at_backend():
    docs = tenant_chart_documents()
    route = next(item for item in docs if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "tenant-mcp")
    backends = [rule["backendRefs"][0] for rule in route["spec"]["rules"]]
    assert backends == [
        {"name": "tenant-api", "namespace": "tenant-infrastructure", "port": 80},
        {"name": "tenant-api", "namespace": "tenant-infrastructure", "port": 80},
    ]
    grant = next(item for item in docs if item["kind"] == "ReferenceGrant" and item["metadata"]["name"] == "tenant-mcp-publicedge")
    assert grant["metadata"]["namespace"] == "tenant-infrastructure"
    assert grant["spec"]["from"] == [
        {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": "toolhive-system"}
    ]
    assert grant["spec"]["to"] == [{"group": "", "kind": "Service", "name": "tenant-api"}]


def test_tenant_resource_claim_schema_is_bounded():
    docs = tenant_chart_documents()
    claim = next(item for item in docs if item["metadata"]["name"].startswith("tenantresourceclaims."))
    claim_schema = claim["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    assert claim_schema["properties"]["spec"]["properties"]["capability"]["enum"] == ["compute.workload.v1", "storage.volume.v1"]
    assert claim_schema["properties"]["status"]["properties"]["phase"]["enum"] == ["Pending", "Provisioning", "Ready", "Updating", "Expiring", "Released", "Failed"]


def test_dex_refresh_rotation_has_bounded_reuse_window():
    script = (ROOT / "clusters/re8ch/components/ai-operations/register-dex-clients.sh").read_text()
    assert 'refresh["reuseInterval"] = "30s"' in script
    assert 'refresh.setdefault("validIfNotUsedFor", "720h")' in script
    assert 'refresh.setdefault("absoluteLifetime", "2160h")' in script
    assert 'refresh.pop("disableRotation", None)' in script


def test_plugin_definition_is_tenant_self_and_denies_financial_mutation():
    plugins = [yaml.safe_load((ROOT / "plugins" / name).read_text()) for name in ("admin.yaml", "tenant.yaml")]
    assert {item["metadata"]["name"] for item in plugins} == {"admin", "tenant"}
    assert next(item for item in plugins if item["metadata"]["name"] == "admin")["spec"]["policy"]["resourceScopes"] == ["admin"]
    for plugin in plugins:
        denied = set(plugin["spec"]["policy"]["deniedFields"])
        assert {"credentialMaterial", "candidateRef", "price", "journalEntry"} <= denied
        assert plugin["spec"]["publication"]["enabled"] is False


def test_generic_tenant_runtime_has_only_admin_and_tenant_profiles():
    root = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
    assert "../../platform/tenant-runtime" not in root
    assert "../../platform/tenant-infrastructure" not in root
    assert "qwen-tenant-runtime" not in root
    docs = tenant_chart_documents()
    deployments = [item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] in {"admin-api", "tenant-api"}]
    profiles = {
        next(env["value"] for env in item["spec"]["template"]["spec"]["containers"][0]["env"] if env["name"] == "RE8CH_TOOL_PROFILE")
        for item in deployments
    }
    assert profiles == {"admin", "tenant"}
    assert all("@sha256:443d9e822230ab0daf59f6c6344caa69843c45d0bb9e78330f34f1d9e3305c6d" in item["spec"]["template"]["spec"]["containers"][0]["image"] for item in deployments)
    rendered = (ROOT / "clusters/re8ch/charts/birfucate-metering/templates/tenant-runtime.yaml").read_text()
    tenant_publication = next(
        item for item in documents(ROOT / "platform/connectool/release.yaml")[1]["spec"]["values"]["toolhive"]["publications"]
        if item["name"] == "tenant-vmcp"
    )
    assert "qwen" not in rendered.lower() + json.dumps(tenant_publication).lower()
    assert "tenant-resource" not in rendered


def test_claim_public_edge_uses_declared_r640_authority():
    gateway = documents(ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml")
    listener = next(item for item in gateway if item["kind"] == "Gateway")["spec"]["listeners"]
    assert any(item["name"] == "apps-wildcard-https" and item["hostname"] == "*.apps.re8ch.com" for item in listener)
    dns = documents(ROOT / "clusters/re8ch/components/public-exposure/external-dns-cloudflare.yaml")
    authority = next(item for item in dns if item.get("metadata", {}).get("name") == "tenant-apps-wildcard-dns-authority")
    assert authority["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == "192.0.2.138"


def test_tenant_compute_has_controlled_metered_egress_gateway():
    docs = documents(ROOT / "clusters/re8ch/components/egress-fabric/identity-egress.yaml")
    policy = next(item for item in docs if item["kind"] == "CiliumEgressGatewayPolicy")
    assert policy["spec"]["selectors"][0]["podSelector"]["matchLabels"]["networking.re8ch.com/egress-class"] == "metered-public"
    assert policy["spec"]["egressGateway"]["nodeSelector"]["matchLabels"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    assert "192.0.2.183/8" in policy["spec"]["excludedCIDRs"]


def test_tenant_bootstrap_binds_controller_only_inside_runtime_namespace():
    script = (ROOT / "operations/scripts/bootstrap-tenant-namespace.sh").read_text()
    assert "name: tenant-resource-provisioner" in script
    assert "name: tenant-control-controller\n  namespace: tenant-infrastructure" in script
    assert "kind: ClusterRole\n  name: tenant-resource-provisioner" in script
