import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def tenant_chart_documents():
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    image = "registry.re8ch.com/image/re8ch-ai-operations@sha256:7bb0fb9142e4e68a50eece9b0a84c06eec6e6618fb270d5cb239dd85fca7a66e"
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
        assert pod["volumes"] == [{"name": "runtime-state", "persistentVolumeClaim": {"claimName": "tenant-platform-state"}}]
        container = pod["containers"][0]
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        profiles[env["RE8CH_TOOL_PROFILE"]] = env["RE8CH_TOOL_AUDIENCE"]
        metadata_urls[env["RE8CH_TOOL_PROFILE"]] = env["PUBLIC_RESOURCE_METADATA_URL"]
        assert container["image"] == "registry.re8ch.com/image/re8ch-ai-operations@sha256:7bb0fb9142e4e68a50eece9b0a84c06eec6e6618fb270d5cb239dd85fca7a66e"
        assert container["imagePullPolicy"] == "IfNotPresent"
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        assert env["DEVICE_REGISTRY_PATH"].startswith("/var/lib/tenant-platform/")
        assert env["TENANT_SERVICE_DB"] == "/var/lib/tenant-platform/tenant-services.db"
        assert container["startupProbe"]["httpGet"]["path"] == "/livez"
        assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
        assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert profiles == {"admin": "re8ch-platform-admin", "tenant": "re8ch-tenant"}
    assert metadata_urls == {
        "admin": "https://admin.re8ch.com:8443/.well-known/oauth-protected-resource/mcp",
        "tenant": "https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/mcp",
    }


def test_admin_vmcp_uses_one_user_boundary_and_private_service_auth():
    docs = tenant_chart_documents()
    admin_tenant = next(item for item in docs if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.") and item["metadata"]["name"] == "admin")
    assert admin_tenant["spec"]["lifecycle"] == "Active"
    admin_binding = next(item for item in docs if item["kind"] == "TenantIdentityBinding" and item["metadata"]["name"] == "admin-vmcp-service")
    assert admin_binding["spec"] == {
        "tenantRef": "admin",
        "issuer": "https://dex.re8ch.com",
        "group": "re8ch:tenant-admins",
        "role": "admin",
        "revoked": False,
    }
    user_binding = next(item for item in docs if item["kind"] == "TenantIdentityBinding" and item["metadata"]["name"] == "admin-github-k8s-admins")
    assert user_binding["spec"] == {
        "tenantRef": "admin",
        "issuer": "https://dex.re8ch.com",
        "group": "re8ch:k8s-admins",
        "role": "admin",
        "revoked": False,
    }
    backend_auth = next(item for item in docs if item["kind"] == "MCPExternalAuthConfig" and item["metadata"]["name"] == "admin-vmcp-backend-auth")
    assert backend_auth["spec"] == {
        "type": "headerInjection",
        "headerInjection": {
            "headerName": "Authorization",
            "valueSecretRef": {"name": "admin-vmcp-backend-auth", "key": "authorizationHeader"},
        },
    }
    entry = next(item for item in docs if item["kind"] == "MCPServerEntry" and item["metadata"]["name"] == "admin-api-backend")
    assert entry["spec"]["externalAuthConfigRef"] == {"name": "admin-vmcp-backend-auth"}
    admin = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] == "admin-api")
    service_token = next(item for item in admin["spec"]["template"]["spec"]["containers"][0]["env"] if item["name"] == "SERVICE_API_TOKEN")
    assert service_token["valueFrom"]["secretKeyRef"] == {"name": "admin-vmcp-backend-auth", "key": "token"}
    tenant_entry = next(item for item in docs if item["kind"] == "MCPServerEntry" and item["metadata"]["name"] == "tenant-api-backend")
    assert tenant_entry["spec"]["externalAuthConfigRef"] == {"name": "admin-vmcp-backend-auth"}
    tenant_api = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] == "tenant-api")
    tenant_service_token = next(item for item in tenant_api["spec"]["template"]["spec"]["containers"][0]["env"] if item["name"] == "SERVICE_API_TOKEN")
    assert tenant_service_token["valueFrom"]["secretKeyRef"] == {"name": "admin-vmcp-backend-auth", "key": "token"}
    policy = next(item for item in docs if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "admin-api")
    source = policy["spec"]["ingress"][0]["from"][0]
    assert source["podSelector"]["matchLabels"] == {"app": "virtualmcpserver", "toolhive-name": "re8ch-admin-vmcp"}

    encrypted_paths = [
        ROOT / "clusters/re8ch/secrets/admin-vmcp-backend-auth-toolhive.sops.yaml",
        ROOT / "clusters/re8ch/secrets/admin-vmcp-backend-auth-tenant.sops.yaml",
    ]
    for encrypted_path in encrypted_paths:
        encrypted = encrypted_path.read_text()
        assert "pending-encryption" not in encrypted
        assert encrypted.count("ENC[AES256_GCM") >= 2
        assert encrypted.count("\nsops:\n") == 1
    root = (ROOT / "clusters/re8ch/kustomization.yaml").read_text()
    assert all(str(path.relative_to(ROOT / "clusters/re8ch")) in root for path in encrypted_paths)


def test_production_tenant_readiness_is_truthful_and_supply_chain_is_optional():
    release = documents(ROOT / "platform/birfucate-metering/release.yaml")[0]
    tenant = release["spec"]["values"]["tenantPlatform"]
    assert tenant["tenantReadinessPath"] == "/readyz"
    assert tenant["supplyChain"] == {"required": False}


def test_tenant_namespace_outlives_helm_remediation_and_protects_sops_secrets():
    root_namespaces = documents(ROOT / "clusters/re8ch/namespaces.yaml")
    namespace = next(item for item in root_namespaces if item["metadata"]["name"] == "tenant-infrastructure")
    assert namespace["metadata"]["labels"]["saas.re8ch.com/tenant"] == "platform"
    chart_docs = tenant_chart_documents()
    assert not any(item["kind"] == "Namespace" for item in chart_docs)
    claims = {item["metadata"]["name"] for item in chart_docs if item["kind"] == "PersistentVolumeClaim"}
    assert "tenant-platform-state" in claims
    state = next(item for item in chart_docs if item["kind"] == "PersistentVolumeClaim" and item["metadata"]["name"] == "tenant-platform-state")
    assert state["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


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
    tenant = next(item for item in docs if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.re8ch.com/") and item["metadata"]["name"] == "artchais")
    bindings = [item for item in docs if item["kind"] == "TenantIdentityBinding" and item["spec"]["tenantRef"] == "artchais"]
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
    policy = next(item for item in docs if item["kind"] == "CiliumNetworkPolicy" and item["metadata"]["name"] == "tenant-api-publicedge")
    assert policy["spec"]["endpointSelector"] == {"matchLabels": {"app": "tenant-api"}}
    assert policy["spec"]["ingress"] == [
        {"fromEntities": ["ingress"], "toPorts": [{"ports": [{"port": "19448", "protocol": "TCP"}]}]}
    ]


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
    assert all(item["spec"]["replicas"] == 1 for item in deployments)
    assert all(item["metadata"]["labels"]["ai.connectool.io/mcp-backend"] == "true" for item in deployments)
    assert all(item["spec"]["template"]["metadata"]["labels"]["ai.connectool.io/mcp-backend"] == "true" for item in deployments)
    assert all(item["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"} for item in deployments)
    assert all("@sha256:7bb0fb9142e4e68a50eece9b0a84c06eec6e6618fb270d5cb239dd85fca7a66e" in item["spec"]["template"]["spec"]["containers"][0]["image"] for item in deployments)
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


def test_chart_binds_controller_in_every_declared_runtime_namespace():
    docs = tenant_chart_documents()
    tenants = {
        item["metadata"]["name"]: item["spec"]["runtimeNamespaceRef"]
        for item in docs
        if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.")
    }
    bindings = {
        item["metadata"]["namespace"]: item
        for item in docs
        if item["kind"] == "RoleBinding" and item["metadata"]["name"] == "tenant-resource-provisioner"
    }

    assert set(tenants.values()) <= set(bindings)
    for tenant, namespace in tenants.items():
        binding = bindings[namespace]
        assert binding["metadata"]["labels"]["tenancy.re8ch.com/tenant"] == tenant
        assert binding["subjects"] == [{
            "kind": "ServiceAccount",
            "name": "tenant-control-controller",
            "namespace": "tenant-infrastructure",
        }]
        assert binding["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "tenant-resource-provisioner",
        }


def test_native_tenant_owner_is_namespaced_and_does_not_own_platform_boundaries():
    docs = tenant_chart_documents()
    roles = [item for item in docs if item["kind"] == "Role" and item["metadata"]["name"] == "tenant-native-owner"]
    assert {item["metadata"]["namespace"] for item in roles} == {"artchais"}
    role = roles[0]
    serialized = json.dumps(role["rules"])
    for forbidden in ("resourcequotas", "limitranges", "networkpolicies", "nodes", "namespaces", "clusterroles", "persistentvolumes"):
        assert forbidden not in serialized
    assert {"deployments", "statefulsets", "daemonsets"} <= {
        resource for rule in role["rules"] for resource in rule["resources"]
    }
    binding = next(item for item in docs if item["kind"] == "RoleBinding" and item["metadata"]["name"] == "tenant-native-owner")
    groups = {item["name"] for item in binding["subjects"] if item["kind"] == "Group"}
    assert groups == {"Artchais:tenant-admins", "Artchais:tenant-operators"}
