import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
STACK = ROOT / "operations/supabase-identity/identity-stack.yaml"


def test_identity_stack_is_separate_and_uses_db_rw_issuer():
    docs = [doc for doc in yaml.safe_load_all(STACK.read_text()) if doc]
    assert all(
        doc["metadata"].get("namespace") == "supabase-identity"
        for doc in docs
        if doc["kind"] != "Namespace"
    )
    assert all(doc["kind"] not in {"HTTPRoute", "Ingress", "Secret"} for doc in docs)
    live_root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../operations/supabase-identity/identity-stack.yaml" not in live_root["resources"]

    config = next(doc for doc in docs if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "supabase-identity-env")
    assert config["data"]["POSTGRES_DB"] == "re8ch_identity"
    assert config["data"]["POSTGRES_HOST"] == "pg-re8ch-primary.pigsty-external.svc.cluster.local"
    assert config["data"]["PGRST_DB_SCHEMAS"] == "api"
    assert config["data"]["API_EXTERNAL_URL"] == "https://db-rw.re8ch.com/auth/v1"
    assert config["data"]["SITE_URL"] == "https://auth.re8ch.com"

    deployments = [doc for doc in docs if doc["kind"] == "Deployment"]
    assert {doc["metadata"]["name"] for doc in deployments} == {
        "supabase-identity-auth", "supabase-identity-rest", "supabase-identity-meta",
        "supabase-identity-studio", "supabase-identity-kong",
    }
    auth = next(doc for doc in deployments if doc["metadata"]["name"] == "supabase-identity-auth")
    pod = auth["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "supabase-identity-harbor-pull"}]
    network = json.loads((ROOT / "operations/network/manifests/network/public-mesh/inventory.json").read_text())
    public_ip = next(node["publicIp"] for node in network["nodes"] if node["name"] == "restricted-node-6dc3f140f9")
    assert pod["hostAliases"] == [
        {"ip": public_ip, "hostnames": ["id.re8ch.com"]},
        {
            "ip": "192.0.2.85",
            "hostnames": ["pg-re8ch-primary.pigsty-external.svc.cluster.local"],
        },
    ]
    assert pod["containers"][0]["image"].startswith("registry.re8ch.com/auth/supabase-auth:v2.196.0@sha256:")
    env = {item["name"]: item for item in pod["containers"][0]["env"]}
    assert env["GOTRUE_OAUTH_SERVER_ENABLED"]["value"] == "true"
    assert env["GOTRUE_OAUTH_SERVER_ALLOW_DYNAMIC_REGISTRATION"]["value"] == "true"
    assert env["GOTRUE_OAUTH_SERVER_AUTHORIZATION_PATH"]["value"] == "/"
    assert env["GOTRUE_API_MAX_REQUEST_DURATION"]["value"] == "30s"
    assert env["GOTRUE_JWT_KEYS"]["valueFrom"]["secretKeyRef"]["name"] == "supabase-identity-secrets"
    assert env["GOTRUE_DISABLE_SIGNUP"]["valueFrom"]["configMapKeyRef"]["key"] == "DISABLE_SIGNUP"
    assert config["data"]["DISABLE_SIGNUP"] == "false"
    assert config["data"]["ENABLE_EMAIL_SIGNUP"] == "true"
    assert env["GOTRUE_HOOK_SEND_EMAIL_ENABLED"]["value"] == "true"
    assert env["GOTRUE_HOOK_SEND_EMAIL_URI"]["value"] == "https://api.re8ch.com/auth/v2/send-email-hook"
    assert env["GOTRUE_HOOK_SEND_EMAIL_SECRETS"]["valueFrom"]["secretKeyRef"] == {
        "name": "supabase-identity-secrets", "key": "SEND_EMAIL_HOOK_SECRET",
    }
    assert env["GOTRUE_EXTERNAL_GITHUB_ENABLED"]["value"] == "true"
    assert env["GOTRUE_EXTERNAL_GITHUB_REDIRECT_URI"]["value"] == "https://auth.re8ch.com/auth/v1/callback"
    assert env["GOTRUE_EXPERIMENTAL_PROVIDER_LINKING_DOMAINS"]["value"] == "github=github,google=google,apple=apple"
    assert env["GOTRUE_SECURITY_MANUAL_LINKING_ENABLED"]["value"] == "true"
    assert config["data"]["ADDITIONAL_REDIRECT_URLS"] == "https://api.re8ch.com/auth/v2/callback"
    for provider in ("GOOGLE", "APPLE"):
        assert env[f"GOTRUE_EXTERNAL_{provider}_ENABLED"]["value"] == "true"
        assert env[f"GOTRUE_EXTERNAL_{provider}_REDIRECT_URI"]["value"] == f"https://api.re8ch.com/auth/callback/{provider.lower()}"
        for suffix in ("CLIENT_ID", "SECRET"):
            key = f"{provider}_OIDC_CLIENT_ID" if suffix == "CLIENT_ID" else f"{provider}_OIDC_CLIENT_SECRET"
            assert env[f"GOTRUE_EXTERNAL_{provider}_{suffix}"]["valueFrom"]["secretKeyRef"]["key"] == key
    assert env["HTTPS_PROXY"]["value"] == env["HTTP_PROXY"]["value"] == "http://mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891"
    assert ".svc" in env["NO_PROXY"]["value"]
    auth_container = pod["containers"][0]
    assert auth_container["startupProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert auth_container["readinessProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert auth_container["livenessProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert auth_container["resources"]["requests"]["cpu"] == "100m"
    assert auth_container["resources"]["limits"]["cpu"] == "500m"
    for name, key in (
        ("GOTRUE_EXTERNAL_GITHUB_CLIENT_ID", "GITHUB_OAUTH_CLIENT_ID"),
        ("GOTRUE_EXTERNAL_GITHUB_SECRET", "GITHUB_OAUTH_CLIENT_SECRET"),
    ):
        assert env[name]["valueFrom"]["secretKeyRef"] == {
            "name": "supabase-identity-secrets", "key": key,
        }

    kong = next(doc for doc in docs if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "supabase-identity-kong-config")
    services = yaml.safe_load(kong["data"]["kong.yml"])["services"]
    assert {service["name"] for service in services} == {
        "auth-v1-open", "auth-v1-open-callback", "auth-v1-open-authorize",
        "auth-v1", "rest-v1", "realtime-v1-ws", "meta", "dashboard",
    }
    realtime = next(service for service in services if service["name"] == "realtime-v1-ws")
    assert realtime["protocol"] == "ws"
    assert realtime["url"] == "http://supabase-identity-realtime:4000/socket"
    assert {plugin["name"] for plugin in realtime["plugins"]} >= {"key-auth", "acl"}
    assert next(service for service in services if service["name"] == "dashboard")["url"] == "http://supabase-identity-studio:3000/"

    clients = list(yaml.safe_load_all((ROOT / "operations/supabase-identity/oauth-clients-headlamp.yaml").read_text()))
    sql = clients[0]["data"]["clients.sql"]
    assert "https://headlamp.service.re8ch.com/oidc-callback" in sql
    assert "'public'" in sql and "'none'" in sql
    assert clients[1]["kind"] == "Job"

    worker = yaml.safe_load((ROOT / "operations/supabase-identity/avatar-worker.yaml").read_text())
    assert worker["kind"] == "Deployment"
    assert worker["metadata"]["namespace"] == "supabase-identity"
    assert worker["spec"]["template"]["spec"]["containers"][0]["image"].startswith(
        "registry.re8ch.com/auth/identity-avatar-worker:20260929@sha256:"
    )
