from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_open_oauth_route_exposes_only_bounded_rpc_paths():
    route = yaml.safe_load((ROOT / "operations/supabase-identity/open-oauth-route.yaml").read_text())
    assert route["spec"]["hostnames"] == ["open.service.re8ch.com"]
    assert route["spec"]["parentRefs"] == [{
        "name": "re8ch-gateway-canary", "namespace": "kube-system", "sectionName": "service-wildcard-https"
    }]
    paths = {}
    for rule in route["spec"]["rules"]:
        match = rule["matches"][0]
        assert match["method"] == "POST"
        assert match["path"]["type"] == "Exact"
        assert rule["backendRefs"] == [{"name": "supabase-identity-rest", "port": 3000}]
        paths[match["path"]["value"]] = rule["filters"][0]["urlRewrite"]["path"]["replaceFullPath"]
    assert paths == {
        "/oauth-applications/v1/register": "/rpc/create_oauth_application",
        "/oauth-applications/v1/list": "/rpc/my_oauth_applications",
        "/oauth-applications/v1/lifecycle": "/rpc/set_oauth_application_lifecycle",
        "/oauth-applications/v1/rotate-secret": "/rpc/rotate_oauth_application_secret",
    }


def test_open_listener_and_identity_domain_include_route():
    docs = [doc for doc in yaml.safe_load_all(
        (ROOT / "clusters/re8ch/components/cilium/gateway-api-canary.yaml").read_text()
    ) if doc]
    gateway = next(doc for doc in docs if doc["kind"] == "Gateway" and doc["metadata"]["name"] == "re8ch-gateway-canary")
    listener = next(item for item in gateway["spec"]["listeners"] if item["name"] == "service-wildcard-https")
    assert listener["hostname"] == "*.service.re8ch.com"
    assert listener["tls"]["certificateRefs"][0]["name"] == "re8ch-service-wildcard-tls"
    kustomization = yaml.safe_load((ROOT / "operations/supabase-identity/kustomization.yaml").read_text())
    assert "open-oauth-route.yaml" in kustomization["resources"]


def test_migration_job_and_postgrest_api_schema_are_deployed():
    config, job = list(yaml.safe_load_all(
        (ROOT / "operations/supabase-identity/oauth-application-runtime.yaml").read_text()
    ))
    assert job["kind"] == "Job"
    assert job["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] == config["metadata"]["name"]
    stack = [doc for doc in yaml.safe_load_all(
        (ROOT / "operations/supabase-identity/identity-stack.yaml").read_text()
    ) if doc]
    env = next(doc for doc in stack if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "supabase-identity-env")
    assert env["data"]["PGRST_DB_SCHEMAS"] == "api"
    assert env["data"]["API_EXTERNAL_URL"] == "https://db-rw.re8ch.com/auth/v1"
