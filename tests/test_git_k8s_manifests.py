from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


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
    assert "components/toolhive/retire-kagent-legacy-mcp.yaml" in root["resources"]


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


def test_all_toolhive_mcp_workloads_are_ha_scheduled_across_a1_b1():
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
    placement = release["spec"]["values"]["toolhive"]["placement"]
    assert placement["nodeSelector"] == {}
    node_expression = placement["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert node_expression["values"] == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
    assert placement["topologySpreadConstraints"][0]["whenUnsatisfiable"] == "DoNotSchedule"

    policies = documents(
        ROOT / "clusters/re8ch/components/toolhive/proxy-scheduling-policy.yaml"
    )
    assert len(policies) == 2
    policy = next(item for item in policies if item["kind"] == "MutatingAdmissionPolicy")
    assert policy["metadata"]["name"] == "toolhive-mcp-a1-b1.re8ch.com"
    expression = policy["spec"]["mutations"][1]["jsonPatch"]["expression"]
    rule = policy["spec"]["matchConstraints"]["resourceRules"][0]
    assert rule["resources"] == ["deployments", "statefulsets"]
    assert "resourceNames" not in rule
    assert "object.metadata.labels['toolhive'] == 'true'" in policy["spec"]["matchConditions"][0]["expression"]
    assert "'restricted-node-1eb0570f74', 'restricted-node-296f36f2fb'" in expression
    assert "object.metadata.labels['toolhive-name']" in expression
    assert "object.metadata.labels['app']" in expression
    assert "podAntiAffinity" in expression
    serialized = (ROOT / "clusters/re8ch/components/toolhive/proxy-scheduling-policy.yaml").read_text()
    assert "restricted-node-6dc3f140f9" not in serialized
    assert "@sha256:" not in serialized

    legacy = documents(ROOT / "clusters/re8ch/components/toolhive/vmcp-external-publication.yaml")
    assert not any(item["kind"] in {"VirtualMCPServer", "MCPOIDCConfig"} for item in legacy)

    mcp_files = [
        "vmcp-external-publication.yaml",
        "external-observe-publication.yaml",
        "external-privileged-publication.yaml",
    ]
    servers = [
        item
        for filename in mcp_files
        for item in documents(ROOT / "clusters/re8ch/components/toolhive" / filename)
        if item["kind"] == "MCPServer"
    ]
    assert len(servers) == 6
    for server in servers:
        assert server["spec"]["replicas"] == 2
        assert server["spec"]["backendReplicas"] == 2
        affinity = server["spec"]["podTemplateSpec"]["spec"]["affinity"]
        values = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
        assert values == ["restricted-node-1eb0570f74", "restricted-node-296f36f2fb"]
        anti = affinity["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
        assert anti["topologyKey"] == "kubernetes.io/hostname"
        assert anti["labelSelector"]["matchLabels"]["toolhive-name"] == server["metadata"]["name"]

    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "components/toolhive/proxy-scheduling-policy.yaml" in root["resources"]
    assert "components/toolhive/vmcp-external-publication.yaml" in root["resources"]
    assert "components/toolhive/external-observe-publication.yaml" in root["resources"]
    assert "components/toolhive/external-privileged-publication.yaml" in root["resources"]
    assert "components/toolhive/retire-kagent-legacy-mcp.yaml" in root["resources"]

    retirement = (ROOT / "clusters/re8ch/retire-legacy-mcp.patch.yaml").read_text()
    assert "name: ops-observe-external" in retirement
    assert "name: ops-operate-external" in retirement
    assert "name: ops-admin-external" in retirement


def test_policy_reviewer_github_app_private_key_is_sops_managed():
    path = ROOT / "clusters/re8ch/flux-system/service-backend-github-policy-reviewer-auth.sops.yaml"
    secret = yaml.safe_load(path.read_text())
    assert secret["metadata"] == {
        "name": "service-backend-github-policy-reviewer-auth",
        "namespace": "flux-system",
    }
    assert set(secret["stringData"]) == {
        "githubAppID",
        "githubAppInstallationIDRe8ch",
        "githubAppInstallationIDAmongClusters",
        "githubAppPrivateKey",
    }
    assert all(value.startswith("ENC[") for value in secret["stringData"].values())
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "flux-system/service-backend-github-policy-reviewer-auth.sops.yaml" in root["resources"]


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
    assert source["spec"]["url"] == "oci://ghcr.io/re8ch/charts/public-edge-manager"
    assert source["spec"]["ref"]["digest"] == (
        "sha256:9ebc10f8ccfdaa41741348be1e5623867a5dbd4f2ad9afa6d95c45cab549d929"
    )
    values = release["spec"]["values"]
    assert values["image"] == {
        "repository": "ghcr.io/re8ch/public-edge-manager",
        "tag": "0.5.1",
        "digest": "sha256:db30ef6b34c4016478844584de18f4f545e20a3dafd532894602b0117df93432",
        "pullPolicy": "IfNotPresent",
    }
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
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert "@sha256:" in pod["containers"][0]["image"]
    cli_volume = next(volume for volume in pod["volumes"] if volume["name"] == "cli")
    assert "@sha256:" in cli_volume["image"]["reference"]


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
        "stalwart-smtp-public": ("mail-smtp", 25),
        "stalwart-smtps-public": ("mail-smtps", 465),
        "stalwart-submission-public": ("mail-submission", 587),
        "stalwart-imaps-public": ("mail-imaps", 993),
        "stalwart-sieve-public": ("mail-sieve", 4190),
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
