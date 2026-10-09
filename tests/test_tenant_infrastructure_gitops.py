import json
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def tenant_chart_documents(provider_enabled=False, tenant_go_enabled=False):
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    image = "registry.re8ch.com/image/re8ch-ai-operations@sha256:2bc3b066876bc6f866b616f713d3413d8207161459749a9fde6d0787978de0a6"
    command = ["helm", "template", "birfucate", str(chart), "--namespace", "observability-ai", "--kube-version", "1.35.0", "--include-crds", "--set", "tenantPlatform.enabled=true", "--set-string", f"tenantPlatform.image={image}"]
    if provider_enabled:
        command.extend(["--set", "tenantPlatform.provider.enabled=true", "--set-string", "tenantPlatform.provider.image=registry.re8ch.com/image/re8ch-ai-operations@sha256:" + "3" * 64])
    if tenant_go_enabled:
        command.extend(["--set", "tenantPlatform.tenantAPI.enabled=true", "--set-string", "tenantPlatform.tenantAPI.image=registry.re8ch.com/image/re8ch-tenant-mcp@sha256:" + "4" * 64])
    rendered = subprocess.run(
        command,
        check=True, capture_output=True, text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(rendered) if item]


def test_go_tenant_api_requires_verified_image_and_isolates_admin_port():
    docs = tenant_chart_documents(tenant_go_enabled=True)
    deployments = {item["metadata"]["name"]: item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] in {"admin-api", "tenant-api"}}
    tenant = deployments["tenant-api"]["spec"]["template"]["spec"]["containers"][0]
    admin = deployments["admin-api"]["spec"]["template"]["spec"]["containers"][0]
    assert tenant["image"] == "registry.re8ch.com/image/re8ch-tenant-mcp@sha256:" + "4" * 64
    tenant_env = {v["name"]: v.get("value") for v in tenant["env"]}
    admin_env = {v["name"]: v.get("value") for v in admin["env"]}
    assert tenant_env["AUTH_DISCOVERY_URL"] == "http://supabase-identity-auth.supabase-identity.svc:9999/.well-known/oauth-authorization-server"
    assert tenant_env["SUPABASE_USER_URL"] == "http://supabase-identity-auth.supabase-identity.svc:9999/user"
    assert admin_env["AUTH_DISCOVERY_URL"] == "https://api.re8ch.com/auth/.well-known/openid-configuration"
    for env in (tenant_env, admin_env):
        assert env["DEX_USERINFO_URL"] == "https://api.re8ch.com/auth/userinfo"

    assert admin["image"].endswith("sha256:2bc3b066876bc6f866b616f713d3413d8207161459749a9fde6d0787978de0a6")
    assert {port["name"] for port in tenant["ports"]} == {"http", "skill-admin"}
    assert next(env["value"] for env in admin["env"] if env["name"] == "DYNAMIC_SKILL_ADMIN_URL") == "http://tenant-api.tenant-infrastructure.svc:19450/internal/dynamic-skills"
    policy = next(item for item in docs if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "tenant-api")
    assert any(rule.get("ports") == [{"protocol": "TCP", "port": 19450}] and rule["from"][0]["podSelector"]["matchLabels"] == {"app": "admin-api"} for rule in policy["spec"]["ingress"])
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    invalid = subprocess.run(["helm", "template", "birfucate", str(chart), "--set", "tenantPlatform.enabled=true", "--set", "tenantPlatform.tenantAPI.enabled=true"], capture_output=True, text=True)
    assert invalid.returncode != 0 and "must be pinned" in invalid.stderr


def test_provider_worker_requires_explicit_signed_image_rollout():
    assert not any(item.get("metadata", {}).get("name") == "tenant-provider-reconciler" for item in tenant_chart_documents())
    docs = tenant_chart_documents(provider_enabled=True)
    worker = next(item for item in docs if item.get("kind") == "Deployment" and item["metadata"]["name"] == "tenant-provider-reconciler")
    container = worker["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.re8ch.com/image/re8ch-ai-operations@sha256:" + "3" * 64
    assert container["env"][1]["valueFrom"]["secretKeyRef"] == {"name": "tenant-provider-identity", "key": "service-role-key"}
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    invalid = subprocess.run(["helm", "template", "birfucate", str(chart), "--set", "tenantPlatform.enabled=true", "--set", "tenantPlatform.provider.enabled=true"], capture_output=True, text=True)
    assert invalid.returncode != 0 and "must be pinned" in invalid.stderr


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
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        pod = deployment["spec"]["template"]["spec"]
        node_terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
        assert node_terms == [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]}]}]
        assert "nodeSelector" not in pod
        assert pod["automountServiceAccountToken"] is (name == "tenant-api")
        assert pod["volumes"] == [{"name": "runtime-state", "persistentVolumeClaim": {"claimName": "tenant-platform-state"}}]
        container = pod["containers"][0]
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        profiles[env["RE8CH_TOOL_PROFILE"]] = env["RE8CH_TOOL_AUDIENCE"]
        metadata_urls[env["RE8CH_TOOL_PROFILE"]] = env["PUBLIC_RESOURCE_METADATA_URL"]
        assert container["image"] == "registry.re8ch.com/image/re8ch-ai-operations@sha256:2bc3b066876bc6f866b616f713d3413d8207161459749a9fde6d0787978de0a6"
        assert container["imagePullPolicy"] == "IfNotPresent"
        env = {item["name"]: item.get("value") for item in container["env"] if "value" in item}
        assert env["DEVICE_REGISTRY_PATH"].startswith("/var/lib/tenant-platform/")
        assert env["TENANT_SERVICE_DB"] == "/var/lib/tenant-platform/tenant-services.db"
        assert container["startupProbe"]["httpGet"]["path"] == "/livez"
        assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
        assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
        assert container["startupProbe"]["timeoutSeconds"] == 3
        assert container["readinessProbe"]["timeoutSeconds"] == 10
        assert container["readinessProbe"]["failureThreshold"] == 3
        assert container["livenessProbe"]["timeoutSeconds"] == 3
    assert profiles == {"admin": "re8ch-platform-admin", "tenant": "re8ch-tenant"}
    assert metadata_urls == {
        "admin": "https://admin.re8ch.com:8443/.well-known/oauth-protected-resource/mcp",
        "tenant": "https://tools.service.re8ch.com/.well-known/oauth-protected-resource/tenant-native/mcp",
    }
    tenant_env = {item["name"]: item.get("value") for item in deployments["tenant-api"]["spec"]["template"]["spec"]["containers"][0]["env"] if "value" in item}
    assert tenant_env["PUBLIC_MCP_URL"] == "https://tools.service.re8ch.com/tenant-native/mcp"
    native_route = next(item for item in docs if item.get("kind") == "HTTPRoute" and item["metadata"]["name"] == "tenant-native-mcp")
    native_paths = {match["path"]["value"] for rule in native_route["spec"]["rules"] for match in rule["matches"]}
    assert native_paths == {"/tenant-native/mcp", "/.well-known/oauth-protected-resource/tenant-native/mcp", "/.well-known/openai-apps-challenge"}
    assert tenant_env["OPENAI_APPS_CHALLENGE_TOKEN"] == ""
    challenge_rule = next(rule for rule in native_route["spec"]["rules"] if rule["matches"][0]["path"]["value"] == "/.well-known/openai-apps-challenge")
    assert challenge_rule["backendRefs"] == [{"name": "tenant-api", "port": 80}]
    assert "filters" not in challenge_rule
    tenant_affinity = deployments["tenant-api"]["spec"]["template"]["spec"]["affinity"]["podAffinity"]
    assert tenant_affinity["requiredDuringSchedulingIgnoredDuringExecution"][0]["labelSelector"]["matchLabels"] == {"app": "admin-api"}


def test_tenant_control_plane_is_ab_ha_and_prefers_local_traffic():
    docs = tenant_chart_documents()
    controller = next(
        item
        for item in docs
        if item["kind"] == "Deployment"
        and item["metadata"]["name"] == "tenant-control-controller"
    )
    assert controller["spec"]["replicas"] == 2
    assert controller["spec"]["progressDeadlineSeconds"] == 180
    assert controller["spec"]["strategy"]["rollingUpdate"] == {
        "maxUnavailable": 1,
        "maxSurge": 0,
    }
    pod_spec = controller["spec"]["template"]["spec"]
    assert "nodeSelector" not in pod_spec
    expression = pod_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
    }
    anti_affinity = pod_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]
    assert anti_affinity["labelSelector"]["matchLabels"] == {
        "app": "tenant-control-controller"
    }
    assert anti_affinity["topologyKey"] == "kubernetes.io/hostname"

    service = next(
        item
        for item in docs
        if item["kind"] == "Service"
        and item["metadata"]["name"] == "tenant-control-controller"
    )
    assert service["spec"]["trafficDistribution"] == "PreferSameNode"
    pdb = next(
        item
        for item in docs
        if item["kind"] == "PodDisruptionBudget"
        and item["metadata"]["name"] == "tenant-control-controller"
    )
    assert pdb["spec"]["minAvailable"] == 1

    container = controller["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"]["timeoutSeconds"] == 3
    assert container["readinessProbe"]["timeoutSeconds"] == 5
    assert container["readinessProbe"]["failureThreshold"] == 3
    assert container["livenessProbe"]["timeoutSeconds"] == 3

    image_cache = next(
        item
        for item in docs
        if item["kind"] == "DaemonSet"
        and item["metadata"]["name"] == "tenant-runtime-image-cache"
    )
    cache_spec = image_cache["spec"]["template"]["spec"]
    assert "nodeSelector" not in cache_spec
    cache_expression = cache_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert cache_expression["values"] == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]


def test_admin_vmcp_uses_one_user_boundary_and_private_service_auth():
    docs = tenant_chart_documents()
    admin_tenant = next(item for item in docs if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.") and item["metadata"]["name"] == "admin")
    assert admin_tenant["spec"]["lifecycle"] == "Active"
    admin_binding = next(item for item in docs if item["kind"] == "TenantIdentityBinding" and item["metadata"]["name"] == "admin-vmcp-service-auth")
    assert admin_binding["spec"] == {
        "tenantRef": "admin",
        "issuer": "https://auth.re8ch.com",
        "group": "re8ch:tenant-admins",
        "role": "admin",
        "revoked": False,
    }
    retired_binding = next(item for item in docs if item["kind"] == "TenantIdentityBinding" and item["metadata"]["name"] == "admin-vmcp-service")
    assert retired_binding["spec"]["issuer"] == "https://dex.service.re8ch.com"
    assert retired_binding["spec"]["revoked"] is True
    user_binding = next(item for item in docs if item["kind"] == "TenantIdentityBinding" and item["metadata"]["name"] == "admin-github-k8s-admins-auth")
    assert user_binding["spec"] == {
        "tenantRef": "admin",
        "issuer": "https://auth.re8ch.com",
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
    assert tenant_entry["spec"]["externalAuthConfigRef"] == {"name": "tenant-vmcp-backend-auth"}
    tenant_backend_auth = next(
        item for item in docs
        if item["kind"] == "MCPExternalAuthConfig"
        and item["metadata"]["name"] == "tenant-vmcp-backend-auth"
    )
    assert tenant_backend_auth["spec"] == {
        "type": "tokenExchange",
        "tokenExchange": {
            "tokenUrl": "http://tenant-api.tenant-infrastructure.svc/oauth/token",
            "audience": "re8ch-tenant",
            "subjectTokenType": "access_token",
        },
    }
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
    assert release["spec"]["install"]["crds"] == "CreateReplace"
    assert release["spec"]["upgrade"]["crds"] == "CreateReplace"
    tenant = release["spec"]["values"]["tenantPlatform"]
    assert tenant["tenantReadinessPath"] == "/readyz"
    assert tenant["supplyChain"] == {"required": False}
    assert tenant["provider"]["enabled"] is True
    assert tenant["provider"]["image"] == tenant["image"]
    assert tenant["image"].startswith("registry.re8ch.com/image/re8ch-ai-operations@sha256:")
    artchais = next(item for item in tenant["tenants"] if item["name"] == "artchais")
    subject_binding = next(
        item for item in artchais["identityBindings"]
        if item["name"] == "artchais-dex-external-operator-v2"
    )
    assert subject_binding == {
        "name": "artchais-dex-external-operator-v2",
        "issuer": "https://dex.service.re8ch.com",
        "subjectHash": "sha256:92948cd9df63b98d534da47c27e236b4b894808feff18558aa53996f7059c415",
        "role": "operator",
    }


def test_review_login_keeps_ordinary_personal_tenant_capabilities():
    release = documents(ROOT / "platform/birfucate-metering/release.yaml")[0]
    tenants = release["spec"]["values"]["tenantPlatform"]["tenants"]
    personal = next(item for item in tenants if item["name"] == "personal-6a68c4a3-96e6-44ef-a0fd-c39fadd4ee38")
    namespace = personal["namespacePolicy"]["defaultNamespace"]
    assert namespace.startswith(personal["namespacePolicy"]["allowedPrefixes"][0])
    assert namespace in personal["namespacePolicy"]["bootstrapNamespaces"]
    assert personal["namespacePolicy"]["allowCreate"] is True
    assert personal["nativeAccess"] is True
    assert personal["byocAllowed"] is True
    assert {grant["consumableRef"] for grant in personal["grants"]} >= {
        "compute.workload.v1", "storage.volume.v1",
        "registry.harbor.v1", "observability.grafana.v1",
    }
    assert personal["identityBindings"] == []  # GoTrue owns ordinary membership.


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
        "tenantnamespaces.tenancy.re8ch.com",
        "tenantidentitybindings.tenancy.re8ch.com",
        "tenantserviceinstances.tenancy.re8ch.com",
        "tenantresourceclaims.tenancy.re8ch.com",
    }
    for crd in docs:
        version = crd["spec"]["versions"][0]
        assert version["subresources"] == {"status": {}}
        assert version["schema"]["openAPIV3Schema"]["type"] == "object"
    tenant = next(item for item in docs if item["metadata"]["name"] == "tenants.tenancy.re8ch.com")
    tenant_spec = tenant["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    assert "runtimeNamespaceRef" not in tenant_spec["required"]
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


def test_tenant_ci_can_request_harbor_from_its_declared_catalog_project():
    docs = tenant_chart_documents()
    tenant = next(item for item in docs if item["kind"] == "Tenant" and item["metadata"]["name"] == "tenant-ci")
    assert tenant["spec"]["harborProjectRef"] == "tenant-ci"
    catalogs = [item for item in yaml.safe_load_all((ROOT / "clusters/re8ch/components/ai-operations/platform.yaml").read_text())
                if item and item.get("kind") == "ConfigMap" and item.get("metadata", {}).get("name") == "harbor-tenant-catalog"]
    assert len(catalogs) == 2
    assert all("tenant-ci" in json.loads(item["data"]["harbor-tenants.json"])["tenants"] for item in catalogs)


def test_connectool_has_one_generic_tenant_publication():
    release = documents(ROOT / "platform/connectool/release.yaml")[1]
    publications = release["spec"]["values"]["toolhive"]["publications"]
    tenant = next(item for item in publications if item["name"] == "tenant-vmcp")
    assert tenant["clientId"] == "2981225a-da08-5aaa-9e84-8fd151696113"
    assert tenant["resourceUrl"] == "https://tools.service.re8ch.com/tenant/mcp"
    assert tenant["workloads"] == [{"name": "tenant-api-backend"}]
    serialized = json.dumps(publications)
    for legacy in ("tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc", "tenant-artchais"):
        assert legacy not in serialized


def test_single_replica_tenant_public_route_bypasses_session_router():
    docs = tenant_chart_documents()
    route = next(item for item in docs if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "tenant-mcp")
    backends = [rule["backendRefs"][0] for rule in route["spec"]["rules"]]
    assert backends == [
        {"name": "vmcp-tenant-vmcp", "port": 4483},
        {"name": "vmcp-tenant-vmcp", "port": 4483},
    ]
    assert not any(item["kind"] == "ReferenceGrant" and item["metadata"]["name"] == "tenant-mcp-publicedge" for item in docs)
    assert not any(item["kind"] == "CiliumNetworkPolicy" and item["metadata"]["name"] == "tenant-api-publicedge" for item in docs)
    backend = next(item for item in docs if item["kind"] == "MCPServerEntry" and item["metadata"]["name"] == "tenant-api-backend")
    assert backend["spec"]["remoteUrl"] == "http://tenant-api.tenant-infrastructure.svc/mcp"
    assert backend["spec"]["externalAuthConfigRef"]["name"] == "tenant-vmcp-backend-auth"


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
    assert all("nodeSelector" not in item["spec"]["template"]["spec"] for item in deployments)
    assert all(
        item["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
        == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
        for item in deployments
    )
    assert all("@sha256:2bc3b066876bc6f866b616f713d3413d8207161459749a9fde6d0787978de0a6" in item["spec"]["template"]["spec"]["containers"][0]["image"] for item in deployments)
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
    apps = next(item for item in listener if item["name"] == "apps-wildcard-https")
    assert apps["hostname"] == "*.apps.re8ch.com"
    assert {"key": "saas.re8ch.com/tenant", "operator": "Exists"} in apps["allowedRoutes"]["namespaces"]["selector"]["matchExpressions"]
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
        if item["kind"] == "Tenant" and item["apiVersion"].startswith("tenancy.") and item["spec"].get("runtimeNamespaceRef")
        and item["spec"]["runtimeNamespaceRef"] not in next(
            tenant["namespacePolicy"]["bootstrapNamespaces"]
            for tenant in documents(ROOT / "clusters/re8ch/charts/birfucate-metering/values.yaml")[0]["tenantPlatform"]["tenants"]
            if tenant["name"] == item["metadata"]["name"]
        )
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


def test_native_tenant_owner_is_reconciled_and_does_not_own_platform_boundaries():
    docs = tenant_chart_documents()
    assert not [item for item in docs if item["kind"] == "Role" and item["metadata"]["name"] == "tenant-native-owner"]
    controller = (ROOT / "clusters/re8ch/components/ai-operations/tenant_control_controller.py").read_text()
    serialized = controller
    for forbidden in ("resourcequotas", "limitranges", "networkpolicies", "nodes", "namespaces", "clusterroles", "persistentvolumes"):
        assert f'"resources": ["{forbidden}"]' not in serialized
    assert all(resource in serialized for resource in ("deployments", "statefulsets", "daemonsets"))
    cluster_role = next(item for item in docs if item["kind"] == "ClusterRole" and item["metadata"]["name"] == "tenant-control-controller")
    delegated = {(tuple(rule.get("resources", [])), tuple(rule.get("resourceNames", []))): set(rule["verbs"]) for rule in cluster_role["rules"]}
    assert delegated[(("roles",), ("tenant-native-owner", "tenant-publicedge-route-manager"))] == {"escalate", "bind"}
    assert delegated[(("clusterroles",), ("tenant-resource-provisioner",))] == {"bind"}
    assert all(
        rule.get("resourceNames") == ["tenant-resource-provisioner"] and rule["verbs"] == ["bind"]
        for rule in cluster_role["rules"] if "clusterroles" in rule.get("resources", [])
    )
    provisioner = next(item for item in docs if item["kind"] == "ClusterRole" and item["metadata"]["name"] == "tenant-resource-provisioner")
    assert any(rule.get("apiGroups") == ["cilium.io"] and rule.get("resources") == ["ciliumnetworkpolicies"] for rule in provisioner["rules"])
    assert not any(rule.get("apiGroups") == ["cilium.io"] for rule in cluster_role["rules"])


def test_grafana_org_sync_can_reach_grafana_and_metrics_auth_selects_only_tenant_users():
    grafana = documents(ROOT / "platform/observability/production/grafana.yaml")
    cron = next(item for item in grafana if item["kind"] == "CronJob" and item["metadata"]["name"] == "re8ch-grafana-org-sync")
    pod_labels = cron["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    policy = next(item for item in grafana if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "re8ch-grafana")
    assert {"podSelector": {"matchLabels": pod_labels}} in policy["spec"]["ingress"][0]["from"]
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "tenant-infrastructure"}},
            "podSelector": {"matchLabels": {"app": "tenant-provider-reconciler"}}} in policy["spec"]["ingress"][0]["from"]

    provider_role = next(item for item in tenant_chart_documents(provider_enabled=True)
                         if item["kind"] == "Role" and item["metadata"]["name"] == "tenant-provider-credential-write")
    assert {"apiGroups": ["operator.victoriametrics.com"], "resources": ["vmusers"],
            "verbs": ["get", "create", "delete"]} in provider_role["rules"]

    auth = documents(ROOT / "platform/observability/production/tenant-metrics-auth.yaml")[0]
    assert auth["kind"] == "VMAuth" and auth["metadata"]["name"] == "tenant-metrics"
    assert auth["spec"]["selectAllByDefault"] is False
    assert auth["spec"]["userNamespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "credential-broker"}
    assert auth["spec"]["userSelector"]["matchLabels"] == {"observability.re8ch.com/tenant-metrics-user": "true"}
    assert "@sha256:" in auth["spec"]["image"]["tag"]


def test_database_metrics_bindings_limit_provider_reads_to_pinned_re8ch_credentials():
    release = documents(ROOT / "platform/birfucate-metering/release.yaml")[0]
    bindings = release["spec"]["values"]["tenantPlatform"]["provider"]["databaseBindings"]
    assert {item["database"] for item in bindings} == {"anysite", "secretary"}
    assert all(item["tenantRef"] == "re8ch" and item["namespace"].startswith("re8ch-") for item in bindings)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as values:
        json.dump(release["spec"]["values"], values)
        values.flush()
        docs = [item for item in yaml.safe_load_all(subprocess.run(
            ["helm", "template", "birfucate", str(ROOT / "clusters/re8ch/charts/birfucate-metering"), "-f", values.name],
            check=True, capture_output=True, text=True).stdout) if item]
    roles = [item for item in docs if item["kind"] == "Role" and item["metadata"]["name"].startswith("tenant-provider-database-metrics-")]
    assert {(item["metadata"]["namespace"], item["rules"][0]["resourceNames"][0]) for item in roles} == {
        (item["namespace"], item["secretName"]) for item in bindings}
    provider = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] == "tenant-provider-reconciler")
    env = {item["name"]: item for item in provider["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert json.loads(env["GRAFANA_DATABASE_BINDINGS_JSON"]["value"]) == bindings


def test_tenant_module_registry_projection_matches_gateway_defaults():
    import json

    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    registry = json.loads((chart / "files/tenant-platform-modules.json").read_text())
    assert registry == json.loads((ROOT / "platform/apps/tenant-mcp/platform-modules.json").read_text())
    for name in ("cloud", "infra", "admin"):
        assert ":8088/mcp" in registry["backends"][name]["url"]
    runtime = (chart / "templates/tenant-runtime.yaml").read_text()
    assert "PLATFORM_MODULE_REGISTRY" in runtime
    assert "mountPath: /etc/re8ch/modules, readOnly: true" in runtime
    assert "subPath: modules.json" not in runtime


def test_shared_session_authority_is_opt_in_and_only_on_go_tenant():
    chart = ROOT / "clusters/re8ch/charts/birfucate-metering"
    image = "registry.re8ch.com/image/re8ch-ai-operations@sha256:" + "1" * 64
    tenant_image = "registry.re8ch.com/image/re8ch-tenant-mcp@sha256:" + "2" * 64
    endpoint = "https://api.re8ch.com/auth/v2/platform/session"
    rendered = subprocess.check_output([
        "helm", "template", "birfucate", str(chart),
        "--set", "tenantPlatform.enabled=true", "--set", "tenantPlatform.image=" + image,
        "--set", "tenantPlatform.tenantAPI.enabled=true", "--set", "tenantPlatform.tenantAPI.image=" + tenant_image,
        "--set", "tenantPlatform.tenantAPI.platformSessionURL=" + endpoint,
    ], text=True)
    docs = list(yaml.safe_load_all(rendered))
    for name in ("tenant-api", "admin-api"):
        deployment = next(d for d in docs if d and d.get("kind") == "Deployment" and d["metadata"]["name"] == name)
        env = {v["name"]: v.get("value") for v in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
        if name == "tenant-api":
            assert env["PLATFORM_SESSION_URL"] == endpoint
        else:
            assert "PLATFORM_SESSION_URL" not in env
    defaults = tenant_chart_documents(tenant_go_enabled=True)
    tenant = next(d for d in defaults if d.get("kind") == "Deployment" and d["metadata"]["name"] == "tenant-api")
    assert all(v["name"] != "PLATFORM_SESSION_URL" for v in tenant["spec"]["template"]["spec"]["containers"][0]["env"])


def test_production_controller_rollout_fits_single_r640_worker():
    release = yaml.safe_load((ROOT / "platform/birfucate-metering/release.yaml").read_text())
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as values:
        yaml.safe_dump(release["spec"]["values"], values)
        values.flush()
        rendered = subprocess.run(["helm", "template", "birfucate", str(ROOT / "clusters/re8ch/charts/birfucate-metering"), "-f", values.name], check=True, capture_output=True, text=True).stdout
    controller = next(item for item in yaml.safe_load_all(rendered) if item and item["kind"] == "Deployment" and item["metadata"]["name"] == "tenant-control-controller")
    assert controller["spec"]["replicas"] == 1
    assert controller["spec"]["strategy"]["rollingUpdate"] == {"maxUnavailable": 1, "maxSurge": 0}
    affinity = controller["spec"]["template"]["spec"]["affinity"]
    assert "podAntiAffinity" not in affinity
    assert affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["restricted-node-6dc3f140f9"]


def test_shared_personal_runtime_class_has_owner_resource_policy():
    release = yaml.safe_load((ROOT / "platform/birfucate-metering/release.yaml").read_text())
    tenants = release["spec"]["values"]["tenantPlatform"]["tenants"]
    shared = next(item for item in tenants if item["name"] == "personal-standard")
    assert shared["identityBindings"] == []
    assert {grant["consumableRef"] for grant in shared["grants"]} == {
        "compute.workload.v1", "storage.volume.v1", "registry.harbor.v1", "observability.grafana.v1"
    }
    assert shared["nativeAccess"] is True
    assert shared["byocAllowed"] is True
    assert shared["namespacePolicy"]["allowCreate"] is True
    assert shared["namespacePolicy"]["bootstrapNamespaces"] == ["personal-standard-default"]
