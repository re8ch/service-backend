from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_auth_v1_routes_to_isolated_supabase_without_replacing_static_root():
    resources = list(yaml.safe_load_all((ROOT / "operations/supabase-identity/public-auth-route.yaml").read_text()))
    route = next(item for item in resources if item["kind"] == "HTTPRoute")
    grant = next(item for item in resources if item["kind"] == "ReferenceGrant")
    assert route["spec"]["hostnames"] == ["auth.re8ch.com"]
    assert route["spec"]["rules"][0]["matches"][0]["path"]["value"] == "/auth/v1"
    assert route["spec"]["rules"][0]["backendRefs"] == [
        {"name": "supabase-identity-auth", "namespace": "supabase-identity", "port": 9999}
    ]
    assert grant["spec"]["from"][0]["namespace"] == "auth"
    assert grant["spec"]["to"][0]["name"] == "supabase-identity-auth"
    center = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/auth/user-center.yaml").read_text()))
    static_route = next(item for item in center if item["kind"] == "HTTPRoute" and item["metadata"]["name"] == "re8ch-user-center")
    assert static_route["spec"]["rules"][-1]["backendRefs"][0]["name"] == "re8ch-user-center"


def test_db_rw_auth_path_preserved_without_retired_postgrest_fallback():
    resources = list(yaml.safe_load_all(
        (ROOT / "operations/supabase-identity/db-rw-auth-route.yaml").read_text()
    ))
    route = next(item for item in resources if item["kind"] == "HTTPRoute")
    grant = next(item for item in resources if item["kind"] == "ReferenceGrant")
    assert route["metadata"]["namespace"] == "supabase-identity"
    assert route["spec"]["hostnames"] == ["db-rw.re8ch.com"]
    assert route["spec"]["rules"][0]["matches"][0]["path"] == {
        "type": "Exact", "value": "/auth/v1/oauth/token"
    }
    assert route["spec"]["rules"][0]["backendRefs"] == [{
        "name": "tenant-api", "namespace": "tenant-infrastructure", "port": 80
    }]
    assert route["spec"]["rules"][0]["filters"][0]["urlRewrite"]["path"] == {
        "type": "ReplaceFullPath", "replaceFullPath": "/oauth/token/compat"
    }
    assert route["spec"]["rules"][1]["matches"][0]["path"] == {
        "type": "PathPrefix", "value": "/auth/v1"
    }
    assert route["spec"]["rules"][1]["backendRefs"] == [{"name": "supabase-identity-auth", "port": 9999}]
    assert route["spec"]["rules"][1]["filters"][0]["urlRewrite"]["path"]["replacePrefixMatch"] == "/"
    assert grant["metadata"]["namespace"] == "tenant-infrastructure"
    assert grant["spec"]["from"] == [{
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": "supabase-identity"
    }]
    assert grant["spec"]["to"] == [{"group": "", "kind": "Service", "name": "tenant-api"}]
    postgrest = list(yaml.safe_load_all((ROOT / "clusters/re8ch/components/observable/postgrest-ha.yaml").read_text()))
    fallback = next(item for item in postgrest if item and item.get("kind") == "HTTPRoute" and item["metadata"]["name"] == "observable-postgrest-rw")
    assert all(match["path"]["value"] != "/" for rule in fallback["spec"]["rules"] for match in rule["matches"])


def test_identity_data_routes_keep_ro_and_rw_separate():
    routes = list(yaml.safe_load_all((ROOT / "operations/supabase-identity/identity-data-routes.yaml").read_text()))
    by_host = {route["spec"]["hostnames"][0]: route for route in routes}
    ro = by_host["db-ro.re8ch.com"]["spec"]["rules"][0]
    rw = by_host["db-rw.re8ch.com"]["spec"]["rules"]
    assert ro["backendRefs"] == [{"name": "supabase-identity-rest-ro", "port": 3000}]
    assert ro["matches"][0]["path"]["value"] == "/identity/rest/v1"
    assert rw[0]["backendRefs"] == [{"name": "supabase-identity-rest", "port": 3000}]
    assert rw[1]["matches"][0]["path"]["value"] == "/realtime/v1"
    assert rw[1]["backendRefs"] == [{"name": "supabase-identity-kong", "port": 8000}]


def test_rfc8414_discovery_reaches_auth_instead_of_postgrest():
    route = next(
        item for item in yaml.safe_load_all(
            (ROOT / "operations/supabase-identity/db-rw-auth-route.yaml").read_text()
        ) if item["kind"] == "HTTPRoute"
    )
    rule = next(rule for rule in route["spec"]["rules"]
                if rule["matches"][0]["path"]["value"] == "/.well-known/oauth-authorization-server/auth/v1")
    assert rule["matches"][0]["path"]["type"] == "Exact"
    assert rule["backendRefs"] == [{"name": "supabase-identity-auth", "port": 9999}]
    assert rule["filters"][0]["urlRewrite"]["path"] == {
        "type": "ReplaceFullPath", "replaceFullPath": "/.well-known/oauth-authorization-server"
    }
