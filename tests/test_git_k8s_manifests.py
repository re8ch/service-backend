from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


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


def test_tools_dns_is_owned_by_public_edge_not_the_http_route_or_gateway():
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
        "networking.re8ch.com/dns-authority": "public-edge"
    }
    assert authority["metadata"]["annotations"]["networking.re8ch.com/provider"] == (
        "public-edge-manager"
    )
    assert authority["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == (
        "192.0.2.138"
    )


def test_toolhive_mcp_workloads_match_capacity_and_node_local_policy():
    release = next(
        item
        for item in documents(ROOT / "platform/connectool/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    publications = release["spec"]["values"]["toolhive"]["publications"]
    assert {item["name"]: item["replicas"] for item in publications} == {
        "re8ch-tools-vmcp": 2,
        "re8ch-admin-vmcp": 2,
        "tenant-vmcp": 2,
    }
    toolhive = release["spec"]["values"]["toolhive"]
    assert toolhive["placement"]["nodeSelector"] == {}
    assert toolhive["runtimePolicy"]["nodeNames"] == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
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
        expected_replicas = 1 if server["metadata"]["name"] == "ops-observe-vmcp-internal" else 2
        assert spec["replicas"] == expected_replicas
        assert spec["backendReplicas"] == expected_replicas
        assert spec["sessionAffinity"] == "ClientIP"
        assert spec["sessionStorage"] == {"provider": "memory"}
        assert next(
            item["value"] for item in spec["env"] if item["name"] == "TRUST_PROXY_CIDRS"
        ) == "192.0.2.57/16"
        expected_generation = (
            "observe-capacity-recovery-2026-09-21"
            if server["metadata"]["name"] == "ops-observe-vmcp-internal"
            else "a1-b1-recovery-2026-09-19"
        )
        assert server["metadata"]["annotations"]["re8ch.com/reconcile-generation"] == expected_generation
        pod_spec = spec["podTemplateSpec"]["spec"]
        expected_annotations = {"scheduling.re8ch.com/placement-revision": "a1-b1-recovery-2026-09-19"}
        if server["metadata"]["name"] == "ops-observe-vmcp-internal":
            expected_annotations["observability.re8ch.com/probe-revision"] = "shared-observability-v1"
        assert spec["podTemplateSpec"]["metadata"]["annotations"] == expected_annotations
        assert "nodeSelector" not in pod_spec
        expression = pod_spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression == {
            "key": "kubernetes.io/hostname",
            "operator": "In",
            "values": ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"],
        }

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
    authority = next(
        item
        for item in documents(ROOT / "clusters/re8ch/components/regional-routing/authority.yaml")
        if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "regional-routing-authority"
    )
    config = authority["data"]
    services = yaml.safe_load(config["SERVICES_JSON"])
    publications = yaml.safe_load(config["PUBLICATION_REFS_JSON"])
    collab = services["collab.tools.re8ch.com."]
    assert collab == {
        "service": "collab",
        "class": "web",
        "probePath": "/.well-known/jmap",
        "acceptedStatuses": [200, 307],
    }
    assert publications["collab"] == {"namespace": "mail-system", "name": "stalwart-collaboration-public"}

    chart_values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/public-edge-manager/values.yaml").read_text()
    )
    assert chart_values["services"]["collab.tools.re8ch.com."] == collab
    assert chart_values["publication"]["refs"]["collab"] == publications["collab"]

    dns_values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/public-edge-manager/values-dns-authority.yaml").read_text()
    )
    assert dns_values["publication"]["refs"]["collab"] == publications["collab"]

    admin = services["stalwart-admin.tools.re8ch.com."]
    assert admin == {
        "service": "stalwart-admin",
        "class": "web",
        "probePath": "/admin",
        "acceptedStatuses": [200, 302],
    }
    admin_ref = {"namespace": "mail-system", "name": "stalwart-admin-public"}
    assert publications["stalwart-admin"] == admin_ref
    assert chart_values["services"]["stalwart-admin.tools.re8ch.com."] == admin
    assert chart_values["publication"]["refs"]["stalwart-admin"] == admin_ref
    assert dns_values["publication"]["refs"]["stalwart-admin"] == admin_ref

    mail = services["mail.re8ch.com."]
    assert mail == {
        "service": "mail",
        "class": "web",
        "probePath": "/api/health",
        "acceptedStatuses": [200],
    }
    assert chart_values["services"]["mail.re8ch.com."] == mail
    assert "mail" not in publications
    assert "mail" not in chart_values["publication"]["refs"]
    assert "mail" not in dns_values["publication"]["refs"]

    release = next(
        item
        for item in documents(ROOT / "platform/public-edge-manager/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    release_values = release["spec"]["values"]
    assert release_values["services"]["mail.re8ch.com."] == mail
    assert "mail" not in release_values["publication"]["refs"]


def test_public_edge_uses_the_public_digest_pinned_controller_supply_chain():
    values = yaml.safe_load(
        (ROOT / "clusters/re8ch/charts/public-edge-manager/values.yaml").read_text()
    )
    assert values["image"] == {
        "repository": "ghcr.io/re8ch/public-edge-manager",
        "tag": "0.3.1",
        "digest": "sha256:557fa1c9c9b56cafa3c0c8c45a7143a2c2931e1e6cbbbd3baaca54bd0f31ecd7",
        "pullPolicy": "IfNotPresent",
    }
    assert values["readinessGates"]["db-rw"]["requiredFields"] == {
        "authority": "patroni"
    }

    authority_docs = documents(
        ROOT / "clusters/re8ch/components/regional-routing/authority.yaml"
    )
    daemonset = next(item for item in authority_docs if item["kind"] == "DaemonSet")
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == (
        "ghcr.io/re8ch/public-edge-manager@"
        "sha256:557fa1c9c9b56cafa3c0c8c45a7143a2c2931e1e6cbbbd3baaca54bd0f31ecd7"
    )
    assert "command" not in container
    assert container["securityContext"]["runAsNonRoot"] is True
    assert all(volume.get("name") != "script" for volume in container.get("volumeMounts", []))

    kustomization = yaml.safe_load(
        (ROOT / "clusters/re8ch/components/regional-routing/kustomization.yaml").read_text()
    )
    assert "configMapGenerator" not in kustomization


def test_public_edge_public_release_requires_fabric_and_node_evidence():
    resources = documents(ROOT / "platform/public-edge-manager/release.yaml")
    source = next(item for item in resources if item["kind"] == "OCIRepository")
    release = next(item for item in resources if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "oci://ghcr.io/public-edge/charts/public-edge-manager"
    assert source["spec"]["ref"]["digest"] == (
        "sha256:8633ef227588f197b5765d89f3f241401de45d3caa07e664a0ba400e86cccd5d"
    )
    values = release["spec"]["values"]
    assert values["image"] == {
        "repository": "ghcr.io/public-edge/public-edge-manager",
        "tag": "0.5.3",
        "digest": "sha256:16e833de14393c619206e6eb15d9f0c50679a6dba50876420cbcb98323a9e3cc",
        "pullPolicy": "IfNotPresent",
    }
    assert values["dns"]["zones"] == [
        "api.re8ch.com.", "db-ro.re8ch.com.", "db-rw.re8ch.com.", "service.re8ch.com."
    ]
    assert not any(name.endswith(".service.re8ch.com.") for name in values["services"])
    assert values["services"]["api.re8ch.com."] == {
        "service": "api", "class": "api", "probePath": "/auth/me", "acceptedStatuses": [200]
    }
    assert values["workload"]["dnsCandidateLabel"] == "networking.re8ch.com/dns-authority-candidate"
    assert values["probe"]["maxWorkers"] == 8
    assert values["fabricEvidence"] == {
        "mode": "Required",
        "apiGroup": "networking.re8ch.com",
        "apiVersion": "v1alpha2",
        "resource": "networkpathassessments",
        "requireNodeReady": True,
        "allowedStates": ["Ready", "Partial"],
    }


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
