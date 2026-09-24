from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_documents(path: str):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def test_mobile_oidc_landing_is_same_origin_and_routes_back_to_cluster():
    documents = load_documents("platform/headlamp/oidc-landing.yaml")
    config = next(doc for doc in documents if doc["kind"] == "ConfigMap")
    service = next(doc for doc in documents if doc["kind"] == "Service")
    nginx = config["data"]["default.conf"]

    assert "location = /auth" in nginx
    assert 'localStorage.setItem("auth_status","success")' in nginx
    assert 'window.opener.location.replace(target)' in nginx
    assert 'var target="/c/re8ch-k3s/"' in nginx
    assert service["spec"]["selector"]["traffic.re8ch.com/headlamp-active"] == "true"


def test_service_route_sends_only_auth_landing_to_helper():
    documents = load_documents("clusters/re8ch/components/cilium/gateway-api-canary.yaml")
    route = next(
        doc
        for doc in documents
        if doc
        and doc.get("kind") == "HTTPRoute"
        and doc["metadata"]["name"] == "headlamp-service-public"
    )

    auth_rule, default_rule = route["spec"]["rules"]
    assert auth_rule["matches"] == [{"path": {"type": "Exact", "value": "/auth"}}]
    assert auth_rule["backendRefs"][0]["name"] == "headlamp-oidc-landing"
    assert default_rule["backendRefs"][0]["name"] == "headlamp"
