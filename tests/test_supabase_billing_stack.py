from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operations/supabase-billing"


def _docs(name: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all((BASE / name).read_text()) if doc]


def test_billing_stack_is_isolated_and_activated() -> None:
    docs = _docs("billing-stack.yaml")
    assert all(
        doc["metadata"].get("namespace") == "supabase-billing"
        for doc in docs
        if doc["kind"] != "Namespace"
    )
    assert all(doc["kind"] != "Secret" for doc in docs)
    config = next(
        doc for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "supabase-billing-env"
    )
    assert config["data"]["POSTGRES_DB"] == "re8ch_billing"
    assert config["data"]["PGRST_DB_SCHEMAS"] == "payment_api,commerce_api"
    assert config["data"]["PGRST_DB_EXTRA_SEARCH_PATH"] == "public,payment,billing,commerce"
    assert config["data"]["DISABLE_SIGNUP"] == "true"
    assert config["data"]["SUPABASE_PUBLIC_URL"] == "https://billing-supabase.re8ch.com"
    assert "re8ch_identity" not in (BASE / "billing-stack.yaml").read_text()

    quota = next(doc for doc in docs if doc["kind"] == "ResourceQuota")
    assert quota["spec"]["hard"] == {
        "requests.cpu": "1500m", "limits.cpu": "5000m",
        "requests.memory": "4096Mi", "limits.memory": "12288Mi",
    }
    pvc = next(doc for doc in docs if doc["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["storageClassName"] == "rbd-r640"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "20Gi"

    identity = yaml.safe_load((ROOT / "clusters/re8ch/domains/identity-access/source/kustomization.yaml").read_text())
    commerce = yaml.safe_load((ROOT / "clusters/re8ch/domains/commerce-billing/source/kustomization.yaml").read_text())
    resource = "../../../../../operations/supabase-billing"
    assert resource in identity["resources"]
    assert resource not in commerce["resources"]


def test_billing_postgrest_has_distinct_rw_ro_contracts() -> None:
    docs = _docs("postgrest-runtime.yaml")
    deployments = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Deployment"}
    assert set(deployments) == {"supabase-billing-rest-rw", "supabase-billing-rest-ro"}
    rw = deployments["supabase-billing-rest-rw"]["spec"]["template"]["spec"]["containers"][0]
    ro = deployments["supabase-billing-rest-ro"]["spec"]["template"]["spec"]["containers"][0]
    rw_env = {item["name"]: item for item in rw["env"]}
    ro_env = {item["name"]: item for item in ro["env"]}
    assert rw_env["PGRST_DB_URI"]["valueFrom"]["secretKeyRef"]["key"] == "PGRST_DB_URI_RW"
    assert ro_env["PGRST_DB_URI"]["valueFrom"]["secretKeyRef"]["key"] == "PGRST_DB_URI_RO"
    assert ro_env["PGRST_DB_CHANNEL_ENABLED"]["value"] == "false"
    assert rw_env["PGRST_DB_SCHEMAS"]["value"] == ro_env["PGRST_DB_SCHEMAS"]["value"] == "payment_api,commerce_api"
    assert rw_env["PGRST_DB_EXTRA_SEARCH_PATH"]["value"] == "public,payment,billing,commerce"
    assert ro_env["PGRST_DB_EXTRA_SEARCH_PATH"]["value"] == "public,payment,billing,commerce"
    assert all("readinessProbe" in container and "livenessProbe" in container for container in (rw, ro))
    for container in (rw, ro):
        env = {item["name"]: item for item in container["env"]}
        assert env["PGRST_ADMIN_SERVER_PORT"]["value"] == "3001"
        assert {port["name"]: port["containerPort"] for port in container["ports"]} == {
            "http": 3000,
            "admin": 3001,
        }
        assert container["startupProbe"]["httpGet"] == {"path": "/ready", "port": "admin"}
        assert container["readinessProbe"]["httpGet"] == {"path": "/ready", "port": "admin"}
        assert container["livenessProbe"]["httpGet"] == {"path": "/live", "port": "admin"}


def test_billing_realtime_allows_slow_first_boot() -> None:
    docs = _docs("billing-stack.yaml")
    deployment = next(
        doc for doc in docs
        if doc["kind"] == "Deployment"
        and doc["metadata"]["name"] == "supabase-billing-realtime"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"] == {
        "tcpSocket": {"port": "http"},
        "periodSeconds": 5,
        "failureThreshold": 60,
    }


def test_billing_studio_uses_dedicated_database_role() -> None:
    docs = _docs("billing-stack.yaml")
    deployment = next(
        doc for doc in docs
        if doc["kind"] == "Deployment"
        and doc["metadata"]["name"] == "supabase-billing-studio"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    assert env["POSTGRES_USER_READ_WRITE"]["value"] == "billing_studio_admin"
    assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "BILLING_META_DB_PASSWORD"
    assert env["PG_META_CRYPTO_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "PG_META_CRYPTO_KEY"


def test_billing_network_policy_tracks_selectorless_pigsty_service() -> None:
    policies = _docs("network-policy.yaml")
    runtime = next(
        doc for doc in policies
        if doc["kind"] == "CiliumNetworkPolicy"
        and doc["metadata"]["name"] == "supabase-billing-pigsty"
    )
    database_rule, local_host_rule = runtime["spec"]["egress"]
    assert database_rule["toServices"] == [{
        "k8sService": {
            "serviceName": "pg-re8ch-primary",
            "namespace": "pigsty-external",
        }
    }]
    assert database_rule["toPorts"] == [{
        "ports": [{"port": "5432", "protocol": "TCP"}]
    }]
    assert local_host_rule == {
        "toEntities": ["host"],
        "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}],
    }
    assert "10.250.0.128/32" not in (BASE / "network-policy.yaml").read_text()


def test_billing_network_policy_allows_gateway_identity_to_kong_only() -> None:
    policies = _docs("network-policy.yaml")
    public_edge = next(
        doc for doc in policies
        if doc["kind"] == "CiliumNetworkPolicy"
        and doc["metadata"]["name"] == "supabase-billing-public-edge"
    )
    assert public_edge["spec"]["endpointSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": "kong"
    }
    assert public_edge["spec"]["ingress"] == [{
        "fromEntities": ["ingress"],
        "toPorts": [{"ports": [{"port": "8000", "protocol": "TCP"}]}],
    }]


def test_billing_routes_split_api_and_admin_hosts() -> None:
    routes = [doc for doc in _docs("public-routes.yaml") if doc["kind"] == "HTTPRoute"]
    assert {tuple(doc["spec"]["hostnames"]) for doc in routes} == {
        ("billing-supabase.re8ch.com",), ("billing-studio.re8ch.com",),
    }
    api = next(doc for doc in routes if doc["metadata"]["name"] == "supabase-billing-api")
    paths = {match["path"]["value"] for match in api["spec"]["rules"][0]["matches"]}
    assert paths == {"/auth/v1", "/rest/v1", "/realtime/v1", "/storage/v1"}


def test_billing_post_start_policy_preserves_identity_boundary() -> None:
    sql = (BASE / "post-start.sql").read_text()
    assert "billing.current_identity_subject()" in sql
    assert "request.jwt.claims" in sql
    assert "identity_subject_map" not in sql
    assert "auth.identities" not in sql
    assert "billing-private" in sql and "public = false" in sql
    assert "billing:user:" in sql
    assert "re8ch_identity" not in sql


def test_stable_db_host_is_path_split_without_cross_database_linking() -> None:
    auth = (ROOT / "clusters/re8ch/components/auth/deployments/auth-postgrest.yaml").read_text()
    commerce = (ROOT / "clusters/re8ch/components/payments/commerce-runtime.yaml").read_text()
    readme = (BASE / "README.md").read_text()
    assert "hostnames: [db-rw.re8ch.com]" in auth
    assert "/auth/rpc" in auth
    assert "hostnames: [db-rw.re8ch.com]" in commerce
    assert "value: /commerce" in commerce
    assert "re8ch_identity" in readme and "re8ch_billing" in readme
    assert "no cross-database foreign key, fdw, dblink" in readme.lower()


def test_browser_native_checkout_uses_api_gateway_and_split_postgrest() -> None:
    commerce = (ROOT / "clusters/re8ch/components/payments/commerce-runtime.yaml").read_text()
    assert 'name: COMMERCE_CHECKOUT_ENABLED, value: "true"' in commerce
    assert 'name: COMMERCE_MINI_CHECKOUT_ENABLED, value: "false"' in commerce
    assert 'name: COMMERCE_RW_URL, value: "https://db-rw.re8ch.com/commerce"' in commerce
    assert 'name: COMMERCE_RO_URL, value: "https://db-ro.re8ch.com/commerce"' in commerce
    assert 'name: WX_PAY_NOTIFY_URL, value: "https://api.re8ch.com/commerce/v1/webhooks/wechat"' in commerce
    assert "hostnames: [api.re8ch.com]" in commerce
    assert "value: /commerce" in commerce
