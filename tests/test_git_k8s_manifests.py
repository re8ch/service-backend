import json
import runpy
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_auth_host_routes_prefixed_login_actions_to_the_broker():
    resources = documents(ROOT / "clusters/re8ch/components/auth/user-center.yaml")
    route = next(
        item for item in resources
        if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "re8ch-user-center"
    )
    oauth_discovery_rule, broker_rule = route["spec"]["rules"][:2]
    assert oauth_discovery_rule["matches"] == [
        {"path": {"type": "Exact", "value": "/.well-known/oauth-authorization-server"}}
    ]
    assert oauth_discovery_rule["backendRefs"] == [
        {"name": "re8ch-auth-gateway", "port": 8080}
    ]
    assert oauth_discovery_rule["filters"] == [
        {
            "type": "URLRewrite",
            "urlRewrite": {
                "path": {
                    "type": "ReplaceFullPath",
                    "replaceFullPath": "/.well-known/openid-configuration",
                }
            },
        }
    ]
    assert broker_rule["backendRefs"] == [{"name": "re8ch-auth-gateway", "port": 8080}]
    matches = {
        (item["path"]["type"], item["path"]["value"])
        for item in broker_rule["matches"]
    }
    assert ("PathPrefix", "/auth") in matches
    assert ("PathPrefix", "/callback") in matches


def test_single_replica_tenant_route_bypasses_the_mcp_session_router():
    resources = documents(
        ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml"
    )
    route = next(
        item for item in resources
        if item["kind"] == "HTTPRoute"
        and item["metadata"]["name"] == "tools-service-tenant-mcp"
    )
    session_rule, discovery_rule = route["spec"]["rules"]
    assert session_rule["backendRefs"] == [
        {"name": "vmcp-tenant-vmcp", "port": 4483}
    ]
    assert discovery_rule["backendRefs"] == [
        {"name": "vmcp-tenant-vmcp", "port": 4483}
    ]


def test_live_nodes_receive_optional_mihomos_placement():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/egress-fabric/live-node-egress-labels.yaml" in root["resources"]
    # Harbor is infrastructure and K3s/containerd must reach it directly.
    # Do not deploy a privileged host installer or couple K3s to Mihomo.
    assert "components/registry-authority/node-installer.yaml" not in root["resources"]

    node_documents = documents(
        ROOT / "clusters/re8ch/components/egress-fabric/live-node-egress-labels.yaml"
    ) + documents(ROOT / "clusters/re8ch/components/rook-ceph/storage-node-labels.yaml")
    live_nodes = {
        "restricted-node-1eb0570f74", "amdwsl-zt", "restricted-node-296f36f2fb", "domain-ex", "muaya-ecs",
        "muaya-lightweight", "overseas-kl", "overseas-la", "overseas-la-2", "overseas-flk", "restricted-node-6dc3f140f9",
        "suxunji", "vm-20-12-ubuntu", "zan",
    }
    by_name = {item["metadata"]["name"]: item for item in node_documents}
    assert set(by_name) == live_nodes
    for node in by_name.values():
        labels = node["metadata"]["labels"]
        assert labels["networking.re8ch.com/mihomo-node"] == "enabled"
        assert labels["networking.re8ch.com/geo"] in {"cn", "global"}



def test_retired_kagent_namespace_is_not_referenced_by_active_gitops_resources():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/toolhive/retire-kagent-legacy-mcp.yaml" not in root["resources"]
    restart_rbac = documents(
        ROOT / "clusters/re8ch/components/toolhive/platform-restart-rbac.yaml"
    )
    assert all(item["metadata"].get("namespace") != "kagent" for item in restart_rbac)


def test_external_dns_is_only_a_public_edge_execution_layer():
    resources = documents(
        ROOT / "clusters/re8ch/components/public-exposure/external-dns-cloudflare.yaml"
    )
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
    assert [arg for arg in args if arg.startswith("--source=")] == ["--source=ingress"]
    assert "--label-filter=networking.re8ch.com/dns-authority=public-edge" in args

    role = next(item for item in resources if item["kind"] == "ClusterRole")
    api_groups = {group for rule in role["rules"] for group in rule["apiGroups"]}
    assert "gateway.networking.k8s.io" not in api_groups

    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/public-exposure/external-dns-cloudflare.yaml" in root["resources"]
    assert "components/toolhive/retire-kagent-legacy-mcp.yaml" not in root["resources"]

    retirement = documents(ROOT / "clusters/re8ch/retire-legacy-mcp.patch.yaml")
    assert not any(
        item.get("kind") == "Deployment"
        and item.get("metadata", {}).get("name") == "external-dns-cloudflare"
        for item in retirement
    )


def test_tools_legacy_dns_writer_is_retired_after_service_zone_publication():
    platform = documents(ROOT / "clusters/re8ch/components/ai-operations/platform.yaml")
    route = next(
        item
        for item in platform
        if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "cloud-platform-tool"
    )
    metadata = route["metadata"]
    assert metadata.get("labels", {}).get("public.re8ch.com/external-dns") is None
    assert not any(key.startswith("external-dns.alpha.kubernetes.io/") for key in metadata["annotations"])
    assert metadata["annotations"]["networking.re8ch.com/dns-authority"] == (
        "toolhive-system/tools-re8ch-com"
    )

    gateways = documents(ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml")
    gateway = next(
        item
        for item in gateways
        if item["kind"] == "Gateway" and item["metadata"]["name"] == "re8ch-gateway-canary"
    )
    assert not any(
        key.startswith("external-dns.alpha.kubernetes.io/")
        for key in gateway["metadata"].get("annotations", {})
    )

    publications = documents(ROOT / "platform/connectool/cloud-route.yaml")
    authority = next(
        item
        for item in publications
        if item["kind"] == "Ingress" and item["metadata"]["name"] == "connectool-tools-re8ch-com"
    )
    assert authority["metadata"]["labels"] == {
        "networking.re8ch.com/dns-authority": "migrated-to-service-zone"
    }
    assert authority["metadata"]["annotations"]["networking.re8ch.com/provider"] == (
        "public-edge-manager"
    )
    assert authority["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == (
        "192.0.2.79"
    )


def test_toolhive_mcp_workloads_match_capacity_and_node_local_policy():
    release = next(
        item
        for item in documents(ROOT / "platform/connectool/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    publications = release["spec"]["values"]["toolhive"]["publications"]
    assert {item["name"]: item["replicas"] for item in publications} == {
        "re8ch-tools-vmcp": 1,
        "re8ch-admin-vmcp": 1,
        "tenant-vmcp": 1,
    }
    toolhive = release["spec"]["values"]["toolhive"]
    assert toolhive["placement"]["nodeSelector"] == {
        "scheduling.re8ch.com/pool-service-core-local": "true",
        "scheduling.re8ch.com/network-serving": "true",
    }
    assert toolhive["runtimePolicy"]["nodeNames"] == ["restricted-node-6dc3f140f9"]
    assert toolhive["runtimePolicy"]["trafficDistribution"] == "PreferSameNode"
    assert toolhive["runtimePolicy"]["backendSessionAffinity"] == "None"

    internal = documents(ROOT / "clusters/re8ch/components/toolhive/vmcp-external-publication.yaml")
    assert not any(item["kind"] in {"VirtualMCPServer", "MCPOIDCConfig", "MCPServerEntry"} for item in internal)
    servers = [item for item in internal if item["kind"] == "MCPServer"]
    assert {item["metadata"]["name"] for item in servers} == {
        "ops-admin-vmcp-internal",
        "ops-observe-vmcp-internal",
        "ops-operate-vmcp-internal",
    }
    for server in servers:
        spec = server["spec"]
        assert spec["replicas"] == 1
        assert spec["backendReplicas"] == 1
        assert spec["sessionAffinity"] == "ClientIP"
        assert spec["sessionStorage"] == {"provider": "memory"}
        assert next(
            item["value"] for item in spec["env"] if item["name"] == "TRUST_PROXY_CIDRS"
        ) == "192.0.2.224/16"
        assert (
            server["metadata"]["annotations"]["re8ch.com/reconcile-generation"]
            == "r640-fallback-2026-09-24"
        )
        pod_spec = spec["podTemplateSpec"]["spec"]
        expected_annotations = {
            "scheduling.re8ch.com/placement-class": "service-core-r640",
            "scheduling.re8ch.com/placement-revision": "workload-contract-v1",
        }
        if server["metadata"]["name"] == "ops-observe-vmcp-internal":
            expected_annotations["observability.re8ch.com/probe-revision"] = "shared-observability-v1"
        assert spec["podTemplateSpec"]["metadata"]["annotations"] == expected_annotations
        assert pod_spec["nodeSelector"] == {
            "scheduling.re8ch.com/pool-service-core-r640": "true",
            "scheduling.re8ch.com/network-serving": "true",
        }
        assert "nodeAffinity" not in pod_spec["affinity"]

    auth = {item["metadata"]["name"]: item for item in internal if item["kind"] == "MCPExternalAuthConfig"}
    assert auth["vmcp-observe-backend-auth"]["spec"]["headerInjection"]["valueSecretRef"] == {
        "name": "ops-mcp-observe-upstream", "key": "x-ops-token"
    }
    assert auth["vmcp-admin-backend-auth"]["spec"]["headerInjection"]["valueSecretRef"] == {
        "name": "ops-mcp-admin-upstream", "key": "x-ops-token"
    }
    observe = next(item for item in servers if item["metadata"]["name"] == "ops-observe-vmcp-internal")
    metrics = next(item["value"] for item in observe["spec"]["env"] if item["name"] == "METRICS_URL")
    assert metrics == "http://vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus"
    tool_configs = {item["metadata"]["name"] for item in internal if item["kind"] == "MCPToolConfig"}
    assert tool_configs == {"ops-operate-tools", "ops-admin-tools"}
    telemetry = next(item for item in internal if item["kind"] == "MCPTelemetryConfig")
    assert telemetry["spec"] == {"openTelemetry": {"enabled": False}, "prometheus": {"enabled": True}}
    backend_policy = next(item for item in internal if item["kind"] == "NetworkPolicy")
    selectors = backend_policy["spec"]["podSelector"]["matchExpressions"]
    assert {selector["key"] for selector in selectors} == {"toolhive-name", "app"}
    assert "mcpserver" not in next(selector["values"] for selector in selectors if selector["key"] == "app")

    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/toolhive/proxy-scheduling-policy.yaml" not in root["resources"]
    assert "components/toolhive/legacy-session-redis.yaml" not in root["resources"]
    assert "components/toolhive/retire-legacy-session-redis.yaml" in root["resources"]
    assert "components/toolhive/vmcp-external-publication.yaml" in root["resources"]
    assert "components/toolhive/external-observe-publication.yaml" in root["resources"]
    assert "components/toolhive/external-privileged-publication.yaml" in root["resources"]
    assert "components/toolhive/retire-kagent-legacy-mcp.yaml" not in root["resources"]
    assert "components/toolhive/operator-release.yaml" in root["resources"]
    assert "components/plugin-runtime/runtime.yaml" in root["resources"]
    assert "components/plugin-runtime/rbac.yaml" in root["resources"]

    redis_retirement = (ROOT / "clusters/re8ch/components/toolhive/retire-legacy-session-redis.yaml").read_text()
    assert "retire-toolhive-session-redis-v3" in redis_retirement
    assert "--wait=false" in redis_retirement
    assert "toolhive-auth-redis-ab" in redis_retirement
    assert "data-toolhive-auth-redis-ab-0" in redis_retirement


def test_mcp_registry_database_is_ab_only():
    resources = documents(ROOT / "clusters/re8ch/components/mcp-registry/registry.yaml")
    database = next(
        item
        for item in resources
        if item["kind"] == "Deployment"
        and item["metadata"]["name"] == "mcp-registry-postgresql"
    )
    expression = database["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
    }
    claim = database["spec"]["template"]["spec"]["volumes"][0][
        "persistentVolumeClaim"
    ]["claimName"]
    assert claim == "mcp-registry-postgresql-ab"

    registry = next(
        item
        for item in resources
        if item["kind"] == "Deployment"
        and item["metadata"]["name"] == "mcp-registry"
    )
    assert registry["spec"]["replicas"] == 2
    assert registry["spec"]["progressDeadlineSeconds"] == 180
    assert registry["spec"]["strategy"]["rollingUpdate"] == {
        "maxUnavailable": 1,
        "maxSurge": 0,
    }
    registry_expression = registry["spec"]["template"]["spec"]["affinity"][
        "nodeAffinity"
    ]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
        "matchExpressions"
    ][0]
    assert registry_expression == {
        "key": "scheduling.re8ch.com/service-plane",
        "operator": "In",
        "values": ["core"],
    }
    service = next(
        item
        for item in resources
        if item["kind"] == "Service" and item["metadata"]["name"] == "mcp-registry"
    )
    assert service["spec"]["trafficDistribution"] == "PreferSameNode"
    pdb = next(
        item
        for item in resources
        if item["kind"] == "PodDisruptionBudget"
        and item["metadata"]["name"] == "mcp-registry"
    )
    assert pdb["spec"]["minAvailable"] == 1


def test_toolhive_controls_and_plugin_runtime_are_worker_ha():
    operator = documents(ROOT / "clusters/re8ch/components/toolhive/operator-release.yaml")
    source = next(item for item in operator if item["kind"] == "OCIRepository")
    release = next(item for item in operator if item["kind"] == "HelmRelease")
    assert source["spec"]["ref"] == {"digest": "sha256:4881731dd7ceaf0fefcdecdb23b1c87d16b1f60fe8aa5ee75304fd33f4fa4d22"}
    values = release["spec"]["values"]["operator"]
    assert values["replicaCount"] == 2
    assert values["image"] == "ghcr.io/stacklok/toolhive/operator@sha256:7998095e6b998d2ae50f93393fa8c969d41a1422457d1775aef81c81dfa24b67"
    assert values["toolhiveRunnerImage"] == "ghcr.io/stacklok/toolhive/proxyrunner@sha256:1ee5a4d66763180fdfdb95c4da9afb7112946e90531516fb9a1fd279f2c9cd29"
    assert values["vmcpImage"] == "ghcr.io/stacklok/toolhive/vmcp@sha256:22b6bb5c642c45e913afef0ea0ca696b1f12cd7dd26d74a8e8cb0b794cd2774c"
    assert values["nodeSelector"] == {}
    expressions = values["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"]
    assert expressions == [
        {
            "key": "scheduling.re8ch.com/service-plane",
            "operator": "In",
            "values": ["core"],
        }
    ]
    pod_anti_affinity = values["affinity"]["podAntiAffinity"]
    assert pod_anti_affinity["requiredDuringSchedulingIgnoredDuringExecution"] == [
        {
            "labelSelector": {
                "matchLabels": {"app.kubernetes.io/name": "toolhive-operator"},
            },
            "topologyKey": "kubernetes.io/hostname",
        }
    ]
    assert release["spec"]["postRenderers"] == [
        {
            "kustomize": {
                "patches": [
                    {
                        "target": {
                            "version": "v1",
                            "kind": "Deployment",
                            "name": "toolhive-operator",
                        },
                        "patch": "- op: add\n  path: /spec/strategy\n  value:\n    type: RollingUpdate\n    rollingUpdate: {maxUnavailable: 1, maxSurge: 0}",
                    }
                ]
            }
        }
    ]

    runtime = documents(ROOT / "clusters/re8ch/components/plugin-runtime/runtime.yaml")
    deployment = next(item for item in runtime if item["kind"] == "Deployment")
    service = next(item for item in runtime if item["kind"] == "Service")
    pdb = next(item for item in runtime if item["kind"] == "PodDisruptionBudget")
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["progressDeadlineSeconds"] == 180
    assert service["spec"]["trafficDistribution"] == "PreferSameNode"
    assert pdb["spec"]["minAvailable"] == 1


def test_embedded_workspace_controller_is_valid_python():
    config_map = next(
        item
        for item in documents(ROOT / "platform/git-workspaces/controller.yaml")
        if item["kind"] == "ConfigMap"
    )
    compile(config_map["data"]["controller.py"], "controller.py", "exec")


def test_work_evidence_requires_kubernetes_producer_identity():
    evidence = next(
        item
        for item in documents(ROOT / "platform/git-workspaces/crds.yaml")
        if item["metadata"]["name"] == "workevidences.governance.re8ch.com"
    )
    required = evidence["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["required"]
    assert {"producerServiceAccount", "observedAt", "plane", "commit", "repository"} <= set(required)


def test_stalwart_management_is_internal_and_digest_pinned():
    resources = documents(ROOT / "platform/stalwart/stalwart.yaml")
    service = next(item for item in resources if item["kind"] == "Service" and item["metadata"]["name"] == "stalwart")
    stateful_set = next(item for item in resources if item["kind"] == "StatefulSet")
    image = stateful_set["spec"]["template"]["spec"]["containers"][0]["image"]
    assert service["spec"]["type"] == "ClusterIP"
    assert "@sha256:" in image


def test_stalwart_idp_pilot_is_isolated_and_uses_asymmetric_oidc_signing():
    resources = documents(ROOT / "platform/stalwart/idp-pilot.yaml")
    stateful_set = next(item for item in resources if item["kind"] == "StatefulSet")
    assert stateful_set["metadata"]["name"] == "stalwart-idp"
    assert stateful_set["spec"]["replicas"] == 1
    assert stateful_set["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    container = stateful_set["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["capabilities"]["add"] == ["NET_BIND_SERVICE"]
    assert {item["name"]: item["value"] for item in container["env"]}["STALWART_PUBLIC_URL"] == (
        "https://id-stalwart.tools.re8ch.com"
    )
    plan = next(
        item
        for item in resources
        if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "stalwart-idp-bootstrap-plan"
    )["data"]["plan.ndjson"]
    assert '"issuerUrl":"https://auth.re8ch.com"' in plan
    assert '"claimGroups":"groups"' in plan
    assert '"anonymousClientRegistration":true' in plan
    assert '"signatureAlgorithm":"es256"' in plan
    assert '"clientId":"kubectl-stalwart-pilot"' in plan
    assert '"clientId":"re8ch-cloud-platform-tool"' in plan
    assert '"clientId":"re8ch-platform-admin"' in plan
    assert '"clientId":"re8ch-tenant"' in plan


def test_stalwart_idp_public_route_exposes_only_login_and_oidc_paths():
    resources = documents(ROOT / "platform/stalwart/idp-pilot.yaml")
    route = next(item for item in resources if item.get("metadata", {}).get("name") == "stalwart-idp-public")
    assert route["spec"]["hostnames"] == ["id-stalwart.tools.re8ch.com"]
    paths = {
        match["path"]["value"]
        for rule in route["spec"]["rules"]
        for match in rule.get("matches", [])
    }
    assert paths == {"/", "/.well-known", "/auth", "/login", "/account", "/api/discover"}
    assert "/api" not in paths


def test_stalwart_public_route_only_exposes_collaboration_paths():
    resources = documents(ROOT / "platform/stalwart/public-collaboration.yaml")
    route = next(item for item in resources if item["metadata"]["name"] == "stalwart-collaboration-public")
    assert route["spec"]["hostnames"] == ["collab.tools.re8ch.com"]
    paths = {
        match["path"]["value"]
        for rule in route["spec"]["rules"]
        for match in rule.get("matches", [])
    }
    assert {"/", "/jmap", "/dav", "/.well-known", "/account", "/auth"} == paths
    assert "/api" not in paths
    root = next(rule for rule in route["spec"]["rules"] if rule.get("matches", [{}])[0].get("path", {}).get("value") == "/")
    assert root["filters"][0]["requestRedirect"]["path"]["replaceFullPath"] == "/account"
    backend = next(rule for rule in route["spec"]["rules"] if "backendRefs" in rule)
    assert backend["backendRefs"][0]["port"] == 8080


def test_stalwart_admin_uses_an_isolated_hostname_and_management_paths():
    resources = documents(ROOT / "platform/stalwart/public-collaboration.yaml")
    route = next(item for item in resources if item["metadata"]["name"] == "stalwart-admin-public")
    assert route["spec"]["hostnames"] == ["stalwart-admin.tools.re8ch.com"]
    paths = {
        match["path"]["value"]
        for rule in route["spec"]["rules"]
        for match in rule.get("matches", [])
    }
    assert paths == {"/", "/admin", "/api", "/jmap", "/login", "/auth", "/.well-known"}
    assert route["metadata"]["annotations"]["governance.re8ch.com/exposure"] == (
        "stalwart-admin-authentication-required"
    )


def test_stalwart_collaboration_is_registered_with_public_edge_manager():
    release = next(
        item
        for item in documents(ROOT / "platform/public-edge-manager/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    values = release["spec"]["values"]
    services = values["services"]
    collab = services["collab.tools.re8ch.com."]
    assert collab == {
        "service": "collab",
        "class": "web",
        "probePath": "/.well-known/jmap",
        "acceptedStatuses": [200, 307],
    }
    admin = services["stalwart-admin.tools.re8ch.com."]
    assert admin == {
        "service": "stalwart-admin",
        "class": "web",
        "probePath": "/admin",
        "acceptedStatuses": [200, 302],
    }
    mail = services["mail.re8ch.com."]
    assert mail == {
        "service": "mail",
        "class": "web",
        "probePath": "/api/health",
        "acceptedStatuses": [200],
    }
    assert values["publication"] == {"enabled": False, "adapters": {}, "refs": {}}


def test_public_edge_uses_the_public_digest_pinned_controller_supply_chain():
    legacy_chart = ROOT / "clusters/re8ch/charts/public-edge-manager"
    assert not [path for path in legacy_chart.glob("**/*") if path.is_file()]

    assert not (ROOT / "clusters/re8ch/components/regional-routing/authority.yaml").exists()

    kustomization = yaml.safe_load(
        (ROOT / "clusters/re8ch/components/regional-routing/kustomization.yaml").read_text()
    )
    assert "configMapGenerator" not in kustomization


def test_public_edge_public_release_requires_fabric_and_node_evidence():
    resources = documents(ROOT / "platform/public-edge-manager/release.yaml")
    source = next(item for item in resources if item["kind"] == "OCIRepository")
    release = next(item for item in resources if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/public-edge/charts/public-edge-manager"
    assert source["spec"]["ref"] == {"digest": "sha256:a0680378f3330cbc5bcbeb918a0d230750f127fbda86ffcdde763b9f02297df5"}
    values = release["spec"]["values"]
    assert values["image"] == {
        "repository": "ghcr.io/public-edge/public-edge-manager",
        "tag": "0.6.11",
        "digest": "sha256:390b5b9d14f9161bc70b26ad456fd72a3e5e8955172ba1da919ed61059972c5a",
        "pullPolicy": "IfNotPresent",
    }
    assert values["dns"]["zones"] == [
        "auth.re8ch.com.", "api.re8ch.com.", "db-ro.re8ch.com.", "db-rw.re8ch.com.",
        "service.re8ch.com.", "functions.re8ch.com."
    ]
    assert values["dns"]["nameserversConfigMap"] == "public-edge-nameservers"
    assert values["services"]["headlamp.service.re8ch.com."]["service"] == "headlamp-service"
    assert values["dns"]["externalRecords"]["_acme-challenge.service.re8ch.com."][0]["value"] == "_acme-challenge.re8ch.com."
    assert values["services"]["api.re8ch.com."] == {
        "service": "api", "class": "api", "probePath": "/auth/me", "acceptedStatuses": [200]
    }
    assert "postRenderers" not in release["spec"]
    assert "authorityNodes" not in values and "publicEdges" not in values
    assert values["discovery"]["replicas"] == 2
    assert values["discovery"]["minimumCapacityMbps"] == 100
    assert values["discovery"]["clusterInventoryConfigMap"] == {
        "namespace": "flux-system", "name": "cluster-node-inventory", "key": "nodes.json",
    }
    assert "nodeInventory" not in values["discovery"]
    assert "capacityInventory" not in values["discovery"]
    assert "dependsOn" not in release["spec"]
    assert values["discovery"]["gatewayNamespace"] == "kube-system"
    assert values["probe"]["maxWorkers"] == 8
    assert values["probe"]["intervalSeconds"] == 15
    assert values["candidateSelection"]["failoverGraceSeconds"] == 300
    assert values["candidateSelection"]["minReadySeconds"] == 120
    assert values["candidateSelection"]["minHoldSeconds"] == 600
    assert values["candidateSelection"]["localNodeBonus"] == 0
    assert values["fabricEvidence"] == {
        "mode": "Required",
        "apiGroup": "networking.re8ch.com",
        "apiVersion": "v1alpha2",
        "resource": "networkpathassessments",
        "requireNodeReady": False,
        "allowedStates": ["Ready", "Partial"],
    }


def test_public_edge_owns_capacity_inventory_without_creating_nodes():
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/regional-routing/public-edge-node-metadata.yaml" not in root["resources"]
    assert "components/rook-ceph/storage-node-labels.yaml" in root["resources"]
    assert not (ROOT / "clusters/re8ch/components/regional-routing/public-edge-node-metadata.yaml").exists()
    regional = yaml.safe_load((ROOT / "clusters/re8ch/components/regional-routing/kustomization.yaml").read_text())
    assert "public-edge-node-metadata.yaml" not in regional["resources"]

    release = next(
        item for item in documents(ROOT / "platform/public-edge-manager/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    inventory = next(
        item for item in documents(ROOT / "clusters/re8ch/components/cluster-inventory/configmap.yaml")
        if item["kind"] == "ConfigMap"
    )
    nodes = json.loads(inventory["data"]["nodes.json"])
    assert nodes["overseas-la"] == {"capacityMbps": 1000, "region": "us-ca"}
    assert nodes["restricted-node-6dc3f140f9"] == {"capacityMbps": 200, "region": "cn-hunan"}
    assert nodes["vm-20-12-ubuntu"] == {"capacityMbps": 3, "region": "cn-guangzhou"}
    assert not {"qwen-1", "qwen-2", "qwen-3"} & nodes.keys()
    assert "dependsOn" not in release["spec"]
    fabric = next(
        item for item in documents(ROOT / "platform/advanced-fabric/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    assert "nodes" not in fabric["spec"]["values"]["advancedFabric"]
    assert fabric["spec"]["valuesFrom"] == [{
        "kind": "ConfigMap", "name": "cluster-node-inventory", "valuesKey": "advanced-fabric-values.yaml",
    }]
    fabric_nodes = yaml.safe_load(inventory["data"]["advanced-fabric-values.yaml"])["advancedFabric"]["nodes"]
    assert {item["name"]: (item["uplinkMbps"], item["region"]) for item in fabric_nodes} == {
        name: (item["capacityMbps"], item["region"]) for name, item in nodes.items()
    }
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/cluster-inventory/configmap.yaml" in root["resources"]
    renderer = runpy.run_path(str(ROOT / "operations/network/scripts/render-cluster-node-inventory.py"))
    assert (ROOT / "clusters/re8ch/components/cluster-inventory/configmap.yaml").read_text() == renderer["render"]()

    release = next(
        item
        for item in documents(ROOT / "platform/advanced-fabric/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    assert release["spec"]["values"]["advancedFabric"]["networkQuality"]["freshnessSeconds"] == 300
    assert release["spec"]["values"]["advancedFabric"]["componentAPI"]["validitySeconds"] == 300


def test_stalwart_bootstrap_is_additive_non_root_and_digest_pinned():
    resources = documents(ROOT / "platform/stalwart/bootstrap-plan.yaml")
    plan = next(item for item in resources if item["kind"] == "ConfigMap")
    job = next(item for item in resources if item["kind"] == "Job")
    lines = [yaml.safe_load(line) for line in plan["data"]["plan.ndjson"].splitlines()]
    assert lines and {line["@type"] for line in lines} <= {"upsert", "update"}
    assert any(line["object"] == "Sharing" for line in lines)
    domain = next(line for line in lines if line["object"] == "Domain")
    assert domain["value"]["phonaid-domain"]["name"] == "phonaid.com"
    assert domain["value"]["phonaid-domain"]["aliases"] == {}
    assert domain["value"]["re8ch-domain"]["name"] == "re8ch.com"
    assert domain["value"]["re8ch-domain"]["aliases"] == {}
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert "@sha256:" in pod["containers"][0]["image"]
    cli_volume = next(volume for volume in pod["volumes"] if volume["name"] == "cli")
    assert "@sha256:" in cli_volume["image"]["reference"]


def test_stalwart_webmail_is_digest_pinned_stateless_and_public():
    resources = documents(ROOT / "platform/stalwart/webmail.yaml")
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    service = next(item for item in resources if item["kind"] == "Service")
    route = next(
        item
        for item in resources
        if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "stalwart-webmail-public"
    )
    dns_authority = next(
        item
        for item in resources
        if item["kind"] == "Ingress" and item["metadata"]["name"] == "stalwart-webmail-public"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    assert "@sha256:" in container["image"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert env["JMAP_SERVER_URL"] == "https://collab.tools.re8ch.com"
    assert env["ALLOW_CUSTOM_JMAP_ENDPOINT"] == "false"
    assert env["SETTINGS_SYNC_ENABLED"] == "false"
    assert env["BULWARK_TELEMETRY"] == "off"
    assert service["spec"]["ports"][0]["port"] == 80
    assert route["spec"]["hostnames"] == ["mail.re8ch.com"]
    assert route["spec"]["parentRefs"][0]["sectionName"] == "mail-https"
    assert dns_authority["spec"]["ingressClassName"] == "external-dns-only"
    assert dns_authority["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/hostname"] == "mail.re8ch.com"
    assert dns_authority["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "stalwart-webmail"


def test_stalwart_mailbox_provisioner_creates_complete_accounts_without_logging_passwords():
    resources = documents(ROOT / "platform/stalwart/mailbox-provisioner.yaml")
    config = next(item for item in resources if item["kind"] == "ConfigMap")
    cronjob = next(item for item in resources if item["kind"] == "CronJob")
    script = config["data"]["provision.py"]
    assert "secrets.token_urlsafe(32)" in script
    assert '\"email\": email, \"username\": email, \"password\": password' in script
    assert '\"@type\": \"User\"' in script
    assert '\"phase\": phase, \"provider\": \"stalwart\"' in script
    assert '\"name\": \"stalwart-admin-credentials\"' in script
    assert '\"roles\": {\"@type\": \"Admin\"}' in script
    assert "print(password)" not in script
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [{"secretRef": {"name": "stalwart-recovery-admin"}}]
    assert "@sha256:" in container["image"]


def test_stalwart_provisioner_models_shared_groups_and_distribution_lists():
    resources = documents(ROOT / "platform/stalwart/mailbox-provisioner.yaml")
    config = next(item for item in resources if item["kind"] == "ConfigMap")
    script = config["data"]["provision.py"]
    assert '"@type": "Group"' in script
    assert '"object": "MailingList"' in script
    assert '"name": "maintainers"' in script
    assert '"name": "postmaster"' in script
    cronjob = next(item for item in resources if item["kind"] == "CronJob")
    pod_spec = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}


def test_stalwart_cloudflare_token_is_mirrored_without_committing_credential_material():
    resources = documents(ROOT / "platform/stalwart/cloudflare-token-sync.yaml")
    config = next(item for item in resources if item["kind"] == "ConfigMap")
    script = config["data"]["sync.py"]
    assert "cloudflare-pg-dns-controller-secret" in script
    assert "stalwart-cloudflare-dns-token" in script
    assert "CF_API_TOKEN" in script
    assert "Secret" not in [item["kind"] for item in resources]
    stateful_set = next(
        item for item in documents(ROOT / "platform/stalwart/stalwart.yaml") if item["kind"] == "StatefulSet"
    )
    pod_spec = stateful_set["spec"]["template"]["spec"]
    token_volume = next(item for item in pod_spec["volumes"] if item["name"] == "cloudflare-token")
    assert token_volume["secret"]["optional"] is True


def test_stalwart_public_mail_uses_declared_gateway_tcp_listeners():
    routes = documents(ROOT / "platform/stalwart/public-mail.yaml")
    expected = {
        "stalwart-smtp-public-v2": ("mail-smtp", 25),
        "stalwart-smtps-public-v2": ("mail-smtps", 465),
        "stalwart-submission-public-v2": ("mail-submission", 587),
        "stalwart-imaps-public-v2": ("mail-imaps", 993),
        "stalwart-sieve-public-v2": ("mail-sieve", 4190),
    }
    assert {item["metadata"]["name"] for item in routes} == set(expected)
    for route in routes:
        listener, port = expected[route["metadata"]["name"]]
        assert route["spec"]["parentRefs"][0]["sectionName"] == listener
        assert route["spec"]["rules"][0]["backendRefs"] == [{"name": "stalwart", "port": port}]


def test_opensandbox_proxy_health_is_local_and_embedded_python_compiles():
    resources = documents(ROOT / "platform/agent-onboarding-runtime/opensandbox-orchestra-proxy.yaml")
    config = next(item for item in resources if item["kind"] == "ConfigMap")
    source = config["data"]["proxy.py"]
    compile(source, "proxy.py", "exec")
    assert 'self.path == "/health"' in source
    assert 'payload = b\'{"status":"ok"}\'' in source
    assert '"RE8CH_TENANT_TOOL_URL": TENANT_TOOL_URL' in source
    assert '"toolResourceRef": self.headers.get("X-RE8CH-Tool-Resource")' in source
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    env = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["TEABLE_INFRA_API_KEY"]["valueFrom"]["secretKeyRef"] == {"name": "teable-infra-api-key", "key": "teable-api-key"}
