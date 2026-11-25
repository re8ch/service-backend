from pathlib import Path

import yaml


def test_payment_database_adapters_have_pinned_patch_and_oom_headroom():
    root = Path(__file__).parents[1]
    docs = yaml.safe_load_all((root / "clusters/re8ch/components/payments/credits-runtime.yaml").read_text())
    adapters = {d["metadata"]["name"]: d for d in docs if d and d["kind"] == "Deployment"
                and d["metadata"]["name"] in {"payment-postgrest", "payment-postgrest-ro"}}
    assert len(adapters) == 2
    for name, deployment in adapters.items():
        pod = deployment["spec"]["template"]["spec"]
        assert pod["nodeSelector"] == {"kubernetes.io/hostname": "r640"}
        container = pod["containers"][0]
        assert container["image"] == "docker.io/postgrest/postgrest:v14.12@sha256:54000f24847d01a2c2302e0041cf0618b875c57fb48507d743cfa9aaa50bf43c"
        assert container["resources"]["requests"]["memory"] == "128Mi"
        assert container["resources"]["limits"]["memory"] == "512Mi"
        env = {e["name"]: e for e in container["env"]}
        assert env["PGRST_DB_URI"]["valueFrom"]["secretKeyRef"] == {
            "name": "billing-payment-postgrest-secrets",
            "key": "PGRST_DB_URI_RO" if name.endswith("-ro") else "PGRST_DB_URI_RW",
        }


def test_retired_payment_database_routes_are_not_declared():
    root = Path(__file__).parents[1]
    legacy = [
        item
        for item in yaml.safe_load_all(
            (root / "clusters/re8ch/domains/main-legacy/commerce-billing/resources.yaml").read_text()
        )
        if item and item.get("kind") == "HTTPRoute"
    ]
    routes = {item["metadata"]["name"]: item for item in legacy}

    assert "re8ch-payment-registry-db-ro" not in routes
    assert "re8ch-payment-registry-db-rw" not in routes


def test_payment_registry_uses_signed_paypal_sandbox_release():
    root = Path(__file__).parents[1]
    deployments = {
        item["metadata"]["name"]: item
        for item in yaml.safe_load_all(
            (root / "clusters/re8ch/components/payments/credits-runtime.yaml").read_text()
        )
        if item and item.get("kind") == "Deployment"
    }
    container = deployments["nuclio-payment-registry-api"]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == (
        "registry.re8ch.com/hyperswitch/processor-payment-registry-api@"
        "sha256:f9feb85ac15f6722a89cc0520c377036f7c748db4a49c3ec09c427fe46ae7df8"
    )
    env = {entry["name"]: entry for entry in container["env"]}
    assert env["PAYPAL_BASE_URL"]["value"] == "https://api-m.sandbox.paypal.com"
    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID"):
        assert env[key]["valueFrom"]["secretKeyRef"] == {
            "name": "payment-registry-secrets",
            "key": key,
        }
