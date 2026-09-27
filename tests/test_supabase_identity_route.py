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
