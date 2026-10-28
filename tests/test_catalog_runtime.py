from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "clusters/re8ch/components/payments/catalog-runtime.yaml"


def objects():
    return list(yaml.safe_load_all(MANIFEST.read_text()))


def test_catalog_runtime_uses_signed_digest_and_existing_billing_secret():
    docs = objects()
    deployment = next(item for item in docs if item["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == (
        "ghcr.io/re8ch-business/catalog@"
        "sha256:193f96aaad40bc1a3ff0032f39bde3c31586a8bf301ff92f3e9e6b40a30b64d3"
    )
    env = {item["name"]: item for item in container["env"]}
    assert env["BILLING_JWT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "billing-payment-postgrest-secrets",
        "key": "PGRST_JWT_SECRET",
    }
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False


def test_catalog_routes_are_bound_to_database_catalog_prefixes():
    routes = {
        item["metadata"]["name"]: item
        for item in objects()
        if item["kind"] == "HTTPRoute"
    }
    for access, hostname in (("ro", "db-ro.re8ch.com"), ("rw", "db-rw.re8ch.com")):
        route = routes[f"re8ch-catalog-db-{access}"]
        assert route["spec"]["hostnames"] == [hostname]
        assert route["spec"]["rules"][0]["matches"][0]["path"] == {
            "type": "PathPrefix",
            "value": "/catalog/v1",
        }
        assert route["spec"]["rules"][0]["backendRefs"] == [
            {"name": "re8ch-catalog", "port": 8080}
        ]
