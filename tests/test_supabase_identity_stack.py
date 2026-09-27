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
    assert pod["hostAliases"] == [{"ip": public_ip, "hostnames": ["id-stalwart.tools.re8ch.com"]}]
    assert pod["containers"][0]["image"].startswith("registry.re8ch.com/auth/supabase-auth:v2.196.0@sha256:")
    env = {item["name"]: item for item in pod["containers"][0]["env"]}
    assert env["GOTRUE_OAUTH_SERVER_ENABLED"]["value"] == "false"
    assert env["GOTRUE_JWT_KEYS"]["valueFrom"]["secretKeyRef"]["name"] == "supabase-identity-secrets"

    kong = next(doc for doc in docs if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "supabase-identity-kong-config")
    services = yaml.safe_load(kong["data"]["kong.yml"])["services"]
    assert {service["name"] for service in services} == {
        "auth-v1-open", "auth-v1-open-callback", "auth-v1-open-authorize",
        "auth-v1", "rest-v1", "meta", "dashboard",
    }
    assert next(service for service in services if service["name"] == "dashboard")["url"] == "http://supabase-identity-studio:3000/"
