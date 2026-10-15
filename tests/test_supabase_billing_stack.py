from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operations/supabase-billing"


def _docs(name: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all((BASE / name).read_text()) if doc]


def test_billing_stack_is_isolated_and_staged() -> None:
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

    root = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())
    assert "../../operations/supabase-billing" not in root["resources"]


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
    assert all("readinessProbe" in container and "livenessProbe" in container for container in (rw, ro))


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
    assert "billing.identity_subject_map" in sql
    assert "references auth.users(id)" in sql
    assert "new.identity_data ->> 'sub'" in sql
    assert "billing-private" in sql and "public = false" in sql
    assert "billing:user:" in sql
    assert "re8ch_identity" not in sql
