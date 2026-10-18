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
    assert service["spec"]["selector"] == {
        "app.kubernetes.io/name": "headlamp-gateway-proxy"
    }


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
    assert default_rule["backendRefs"][0]["name"] == "headlamp-gateway-backend"


def test_gateway_backend_tracks_ready_headlamp_across_nodes():
    documents = load_documents("platform/headlamp/gateway-backend.yaml")
    config = next(doc for doc in documents if doc["kind"] == "ConfigMap")
    service = next(
        doc
        for doc in documents
        if doc["kind"] == "Service"
        and doc["metadata"]["name"] == "headlamp-gateway-backend"
    )
    deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
    origin_names = {
        doc["metadata"]["name"]
        for doc in documents
        if doc["kind"] == "Service" and doc["metadata"]["name"].endswith("-origin")
    }

    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "headlamp-gateway-backend"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["internalTrafficPolicy"] == "Cluster"
    assert service["spec"]["selector"] == {
        "app.kubernetes.io/name": "headlamp-gateway-proxy"
    }
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "protocol": "TCP", "targetPort": "http"}
    ]
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["template"]["spec"].get("hostNetwork") is None
    assert origin_names == {"headlamp-origin", "headlamp-oidc-origin"}

    nginx = config["data"]
    assert "worker_processes 2;" in nginx["nginx.conf"]
    assert "location = /livez" in nginx["default.conf"]
    assert "location = /readyz" in nginx["default.conf"]
    assert "proxy_pass http://headlamp-origin/;" in nginx["default.conf"]

    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert container["resources"]["limits"]["memory"] == "128Mi"
    assert deployment["spec"]["template"]["metadata"]["annotations"] == {
        "operations.re8ch.com/config-revision": "2026-10-19-upstream-resilience"
    }
    assert container["volumeMounts"] == [
        {
            "name": "config",
            "mountPath": "/etc/nginx/nginx.conf",
            "subPath": "nginx.conf",
            "readOnly": True,
        },
        {
            "name": "config",
            "mountPath": "/etc/nginx/conf.d/default.conf",
            "subPath": "conf.d/default.conf",
            "readOnly": True,
        },
    ]

    origins = {
        doc["metadata"]["name"]: doc
        for doc in documents
        if doc["kind"] == "Service" and doc["metadata"]["name"].endswith("-origin")
    }
    for origin in origins.values():
        assert origin["spec"]["selector"] == {
            "app.kubernetes.io/name": "headlamp",
            "traffic.re8ch.com/headlamp-active": "true",
        }
