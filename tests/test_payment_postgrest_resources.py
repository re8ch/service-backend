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


def test_payment_database_domain_routes_remain_flux_owned_guardrails():
    root = Path(__file__).parents[1]
    legacy = [
        item
        for item in yaml.safe_load_all(
            (root / "clusters/re8ch/domains/main-legacy/commerce-billing/resources.yaml").read_text()
        )
        if item and item.get("kind") == "HTTPRoute"
    ]
    routes = {item["metadata"]["name"]: item for item in legacy}

    for access, hostname in (("ro", "db-ro.re8ch.com"), ("rw", "db-rw.re8ch.com")):
        route = routes[f"re8ch-payment-registry-db-{access}"]
        assert route["spec"]["hostnames"] == [hostname]
        assert route["spec"]["rules"] == [{
            "backendRefs": [{"name": "nuclio-payment-registry-api", "port": 8080}],
            "matches": [{"path": {"type": "PathPrefix", "value": "/payment/v1"}}],
        }]
