from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
PLATFORM = ROOT / "platform/incidentrelay"


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_upstream_chart_is_digest_pinned_and_production_workers_are_enabled():
    source, release = documents(PLATFORM / "release.yaml")
    assert source["kind"] == "OCIRepository"
    assert source["spec"]["url"] == "oci://ghcr.io/roxy-wi/incidentrelay-charts/incidentrelay"
    assert source["spec"]["ref"]["digest"] == "sha256:8bb521139db33f008c2a3c35546dcf2d5a583017b9487f6e9c8657124d43fdc9"
    assert release["spec"]["chartRef"] == {"kind": "OCIRepository", "name": "incidentrelay"}
    values = release["spec"]["values"]
    assert values["existingConfigSecret"] == "incidentrelay-config"
    assert values["persistence"]["enabled"] is False
    assert values["scheduler"]["enabled"] is True
    assert values["telegram"]["enabled"] is True
    assert values["slack"]["enabled"] is False
    for component in ("web", "scheduler", "telegram"):
        env = {item["name"]: item["value"] for item in values[component]["extraEnv"]}
        assert env["HTTPS_PROXY"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"
        assert ".cluster.local" in env["NO_PROXY"]
    for component in ("web", "scheduler"):
        expression = values[component]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression == {"key": "kubernetes.io/hostname", "operator": "NotIn", "values": ["restricted-node-6dc3f140f9"]}
    telegram_expression = values["telegram"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert telegram_expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["vm-20-12-ubuntu"],
    }
    rendered_patches = str(release["spec"]["postRenderers"])
    assert "ghcr.io/roxy-wi/incidentrelay@sha256:" in rendered_patches
    assert "automountServiceAccountToken: false" in (PLATFORM / "release.yaml").read_text()


def test_publication_uses_existing_gateway_and_declared_r640_authority():
    resources = documents(PLATFORM / "public-route.yaml")
    https_route = next(item for item in resources if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "incidentrelay-public")
    assert https_route["spec"]["hostnames"] == ["oncall.tools.re8ch.com"]
    assert https_route["spec"]["parentRefs"][0]["sectionName"] == "tools-wildcard-https"
    dns = next(item for item in resources if item["kind"] == "Ingress")
    assert dns["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == "192.0.2.138"
    assert dns["spec"]["ingressClassName"] == "external-dns-only"


def test_cilium_gateway_identity_can_reach_only_the_web_port():
    policy = yaml.safe_load((PLATFORM / "gateway-policy.yaml").read_text())
    assert policy["kind"] == "CiliumNetworkPolicy"
    assert policy["spec"]["endpointSelector"]["matchLabels"]["app.kubernetes.io/component"] == "web"
    ingress = policy["spec"]["ingress"]
    assert ingress[0]["fromEntities"] == ["ingress"]
    assert ingress[0]["toPorts"][0]["ports"] == [{"port": "8080", "protocol": "TCP"}]


def test_telegram_proxy_egress_is_limited_to_the_egress_fabric_namespace():
    policies = documents(PLATFORM / "network-policy.yaml")
    policy = next(item for item in policies if item["metadata"]["name"] == "incidentrelay-allow-required")
    proxy_rule = next(
        rule
        for rule in policy["spec"]["egress"]
        if any(port["port"] == 17891 for port in rule.get("ports", []))
    )
    assert proxy_rule["to"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "egress-fabric"}
            }
        },
        {"ipBlock": {"cidr": "192.0.2.20/24"}},
        {"ipBlock": {"cidr": "192.0.2.65/16"}},
    ]
    host_policy = next(
        item
        for item in policies
        if item["metadata"]["name"] == "incidentrelay-allow-node-proxy"
    )
    assert host_policy["kind"] == "CiliumNetworkPolicy"
    assert host_policy["spec"]["endpointSelector"] == {}
    assert host_policy["spec"]["egress"] == [{
        "toEntities": ["host"],
        "toPorts": [{"ports": [{"port": "17891", "protocol": "TCP"}]}],
    }]


def test_secrets_are_sops_encrypted_and_referenced_from_root():
    for name in (
        "incidentrelay-config.sops.yaml",
        "incidentrelay-db-bootstrap.sops.yaml",
        "incidentrelay-bootstrap-admin.sops.yaml",
        "incidentrelay-alertmanager-tokens.sops.yaml",
    ):
        contents = (ROOT / "clusters/re8ch/secrets" / name).read_text()
        assert "ENC[AES256_GCM" in contents
        assert "\nsops:\n" in contents
        assert "REPLACED_BY_SOPS" not in contents
        assert "__DB_PASSWORD__" not in contents
    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../../platform/incidentrelay" in root["resources"]
    assert "secrets/incidentrelay-config.sops.yaml" in root["resources"]
    assert "secrets/incidentrelay-alertmanager-tokens.sops.yaml" in root["resources"]


def test_bootstrap_jobs_do_not_embed_credentials_and_use_immutable_images():
    database_job = yaml.safe_load((PLATFORM / "database-bootstrap.yaml").read_text())
    admin_job = yaml.safe_load((PLATFORM / "bootstrap-admin.yaml").read_text())
    assert database_job["metadata"]["name"] == "incidentrelay-database-bootstrap-v7"
    assert admin_job["metadata"]["name"] == "incidentrelay-bootstrap-admin-v3"
    for job in (database_job, admin_job):
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert "@sha256:" in container["image"]
        assert all("valueFrom" in item or item["name"] == "INCIDENTRELAY_CONFIG_FILE" for item in container["env"])
        assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    database_resources = database_job["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert set(database_resources) == {"requests", "limits"}
    assert set(database_resources["requests"]) == {"cpu", "memory"}
    assert set(database_resources["limits"]) == {"cpu", "memory"}
    assert database_job["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == 999
    assert database_job["spec"]["template"]["spec"]["securityContext"]["runAsGroup"] == 999
    database_container = database_job["spec"]["template"]["spec"]["containers"][0]
    admin_user = next(item for item in database_container["env"] if item["name"] == "POSTGRES_ADMIN_USER")
    assert admin_user["valueFrom"]["secretKeyRef"]["key"] == "POSTGRES_USER_READ_WRITE"
    assert '-U "$POSTGRES_ADMIN_USER"' in database_container["args"][0]
    assert "NOSUPERUSER" not in database_container["args"][0]
    assert 'GRANT incidentrelay TO :"admin_user";' in database_container["args"][0]
    assert 'REVOKE incidentrelay FROM :"admin_user";' in database_container["args"][0]


def test_database_bootstrap_has_quota_headroom_and_avoids_unhealthy_r640_runtime():
    quota = yaml.safe_load((PLATFORM / "database-quota.yaml").read_text())
    assert quota["spec"]["hard"]["limits.cpu"] == "1250m"
    for name in ("database-bootstrap.yaml", "bootstrap-admin.yaml"):
        job = yaml.safe_load((PLATFORM / name).read_text())
        expression = job["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]
        assert expression == {"key": "kubernetes.io/hostname", "operator": "NotIn", "values": ["restricted-node-6dc3f140f9"]}
