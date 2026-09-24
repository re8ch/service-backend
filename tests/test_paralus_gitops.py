from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path: Path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def test_paralus_release_is_pinned_and_uses_cluster_publication_stack():
    release = next(
        item
        for item in documents(ROOT / "platform/paralus/release.yaml")
        if item["kind"] == "HelmRelease"
    )
    chart = release["spec"]["chart"]["spec"]
    assert chart["chart"] == "./platform/paralus/vendor/ztka"
    assert chart["sourceRef"] == {
        "kind": "GitRepository",
        "name": "service-backend",
        "namespace": "flux-system",
    }
    vendored = yaml.safe_load((ROOT / "platform/paralus/vendor/ztka/Chart.yaml").read_text())
    assert vendored["version"] == "0.3.1-re8ch.6"
    assert vendored["appVersion"] == "v0.3.0"
    values = release["spec"]["values"]
    assert values["fqdn"]["domain"] == "paralus.re8ch.com"
    assert values["fqdn"]["consoleHost"] == "paralus.service.re8ch.com"
    assert values["deploy"]["contour"]["enable"] is False
    assert values["ingress"]["enabled"] is False
    assert values["analytics"]["enable"] is False
    assert values["postgresql"]["primary"]["persistence"]["storageClass"] == "rbd-r640"
    assert values["postgresql"]["auth"]["secretKeys"]["userPasswordKey"] == "DB_PASSWORD"
    assert values["kratos"]["secret"]["nameOverride"] == "paralus-db"
    assert values["replicaCount"] == 2
    assert values["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    assert values["postgresql"]["primary"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    assert values["kratos"]["deployment"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    assert values["kratos"]["statefulSet"]["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}

    patches = release["spec"]["postRenderers"][0]["kustomize"]["patches"]
    assert {item["target"]["kind"] for item in patches} == {
        "Deployment",
        "StatefulSet",
        "Job",
    }
    placement = [item for item in patches if item["target"].get("name") is None]
    assert all("path: /spec/template/spec/nodeName" in item["patch"] for item in placement)
    assert all("value: restricted-node-6dc3f140f9" in item["patch"] for item in placement)
    kratos = next(item for item in patches if item["target"].get("name") == "paralus-kratos")
    assert "hostnames: [dex.re8ch.com]" in kratos["patch"]


def test_paralus_routes_preserve_console_path_routing_and_tls_passthrough():
    resources = documents(ROOT / "platform/paralus/public-routes.yaml")
    console = next(item for item in resources if item["kind"] == "HTTPRoute")
    assert console["spec"]["hostnames"] == ["paralus.service.re8ch.com"]
    assert console["spec"]["parentRefs"][0]["sectionName"] == "service-wildcard-https"
    backends = {
        ref["name"]
        for rule in console["spec"]["rules"]
        for ref in rule["backendRefs"]
    }
    assert backends == {"paralus", "prompt", "paralus-kratos-public", "dashboard"}

    tls_routes = [item for item in resources if item["kind"] == "TLSRoute"]
    assert {item["spec"]["parentRefs"][0]["sectionName"] for item in tls_routes} == {
        "paralus-user-tls",
        "paralus-connector-tls",
    }
    assert all(item["spec"]["rules"][0]["backendRefs"] == [{"name": "relay-server", "port": 443}] for item in tls_routes)


def test_paralus_dns_uses_declared_r640_public_authority():
    resources = documents(ROOT / "platform/paralus/public-routes.yaml")
    dns = next(item for item in resources if item["kind"] == "Ingress")
    assert dns["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == "192.0.2.138"
    assert dns["spec"]["ingressClassName"] == "external-dns-only"

    gateway = documents(ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml")
    certificate = next(item for item in gateway if item.get("metadata", {}).get("name") == "paralus-public-gateway")
    assert set(certificate["spec"]["dnsNames"]) == {
        "console.paralus.re8ch.com",
        "*.user.paralus.re8ch.com",
        "*.core-connector.paralus.re8ch.com",
    }
    public_gateway = next(item for item in gateway if item["kind"] == "Gateway")
    listeners = {item["name"]: item for item in public_gateway["spec"]["listeners"]}
    assert listeners["paralus-console-https"]["tls"]["mode"] == "Terminate"
    assert listeners["paralus-user-tls"]["tls"]["mode"] == "Passthrough"
    assert listeners["paralus-connector-tls"]["tls"]["mode"] == "Passthrough"


def test_paralus_is_in_the_production_kustomization():
    cluster = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../../platform/paralus" in cluster["resources"]
    component = yaml.safe_load((ROOT / "platform/paralus/kustomization.yaml").read_text())
    assert set(component["resources"]) == {
        "release.yaml", "public-routes.yaml", "identity-control-plane.yaml"}


def test_paralus_identity_contract_is_gitops_managed_and_least_privilege():
    resources = documents(ROOT / "platform/paralus/identity-control-plane.yaml")
    contract_cm = next(item for item in resources if item["kind"] == "ConfigMap")
    contract = __import__("json").loads(contract_cm["data"]["contract.json"])
    provider = contract["provider"]
    assert provider["metadata"]["name"] == "re8ch-dex"
    assert provider["spec"]["issuerUrl"] == "https://dex.service.re8ch.com"
    assert provider["spec"]["clientId"] == "paralus-service"
    assert provider["spec"]["scopes"] == ["openid", "profile", "email", "groups"]
    assert "clientSecret" not in provider["spec"]

    groups = {item["metadata"]["name"]: item for item in contract["groups"]}
    assert set(groups) == {
        "re8ch:paralus-viewers",
        "re8ch:paralus-cluster-admins",
        "re8ch:paralus-admins",
    }
    assert groups["re8ch:paralus-viewers"]["spec"]["projectNamespaceRoles"][0]["role"] == "PROJECT_READ_ONLY"
    assert groups["re8ch:paralus-cluster-admins"]["spec"]["projectNamespaceRoles"][0]["role"] == "CLUSTER_ADMIN"
    assert groups["re8ch:paralus-admins"]["spec"]["projectNamespaceRoles"][0]["role"] == "ADMIN"

    role = next(item for item in resources if item["kind"] == "Role")
    assert role["rules"] == [{
        "apiGroups": [""], "resources": ["secrets"],
        "resourceNames": ["paralus-oidc-client"], "verbs": ["get"]}]
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "@sha256:" in container["image"]
    assert {item["name"] for item in container["env"]} == {
        "PARALUS_API", "PARALUS_API_KEY_ID", "PARALUS_API_TOKEN"}
    script = contract_cm["data"]["reconcile.py"]
    assert "/auth/v3/sso/oidc/provider" in script
    assert 'prefix + "/groups"' in script
    assert 'prefix + "/group"' in script
    assert "clientSecret" in script
    assert "logging.info" in script and "PARALUS_API_TOKEN" not in script.split("logging.info", 1)[1]
    compile(script, "reconcile.py", "exec")


def test_direct_kubernetes_admin_is_group_only_break_glass():
    binding = yaml.safe_load((ROOT / "platform/headlamp/oidc-admin-rbac.yaml").read_text())
    assert binding["roleRef"]["name"] == "cluster-admin"
    assert binding["subjects"] == [{
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Group",
        "name": "oidc:re8ch:k8s-admins",
    }]
    assert binding["metadata"]["annotations"]["re8ch.com/access-contract"] == (
        "direct-kubernetes-superuser-not-for-product-users")
