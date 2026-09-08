from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def documents(path):
    return [item for item in yaml.safe_load_all((ROOT / path).read_text()) if item]


def test_batch_one_publications_no_longer_create_traefik_routes():
    expected_dns_only = {
        ("ai-operations", "steel-browser"),
        ("dataspace", "storage-collaborator"),
        ("opensandbox-system", "opensandbox-server"),
    }
    paths = [
        "clusters/re8ch/components/public-exposure/gateway-dns-authorities.yaml",
        "clusters/re8ch/components/ai-operations/agent-workspace/steel-browser-public.yaml",
        "clusters/re8ch/components/dataspace/gateway.yaml",
        "clusters/re8ch/components/public-exposure/opensandbox-ingress.yaml",
        "clusters/re8ch/components/llm-gateway/litellm.yaml",
    ]
    ingresses = [
        item
        for path in paths
        for item in documents(path)
        if item.get("kind") == "Ingress"
    ]
    classes = {
        (item["metadata"]["namespace"], item["metadata"]["name"]): item["spec"].get(
            "ingressClassName"
        )
        for item in ingresses
    }

    assert all(classes[name] == "external-dns-only" for name in expected_dns_only)
    assert ("llm-gateway", "litellm-admin") not in classes
    assert "traefik" not in classes.values()


def test_batch_one_gateway_routes_remain_declared():
    route_paths = [
        "clusters/re8ch/components/regional-routing/migrated-public-http-routes.yaml",
        "clusters/re8ch/components/cilium/gateway-api-canary.yaml",
    ]
    routes = {
        (item["metadata"]["namespace"], item["metadata"]["name"])
        for path in route_paths
        for item in documents(path)
        if item.get("kind") == "HTTPRoute"
    }

    assert {
        ("ai-operations", "steel-browser-public"),
        ("dataspace", "storage-collaborator-public"),
        ("llm-gateway", "litellm-public"),
        ("opensandbox-system", "opensandbox-public"),
    }.issubset(routes)


def test_retired_incluster_products_are_not_public_gateway_dependencies():
    resources = documents("clusters/re8ch/components/cilium/gateway-api-canary.yaml")
    gateway = next(item for item in resources if item.get("kind") == "Gateway")
    listener_names = {item["name"] for item in gateway["spec"]["listeners"]}
    routes = {
        (item["metadata"].get("namespace"), item["metadata"]["name"])
        for item in resources
        if item.get("kind") == "HTTPRoute"
    }

    assert "teable-https" not in listener_names
    assert "qianwen-api-https" not in listener_names
    assert ("qwen", "qianwen-api-public-edge") not in routes

    advanced = next(
        item
        for item in documents("platform/advanced-fabric/release.yaml")
        if item.get("kind") == "HelmRelease"
    )
    assert advanced["spec"]["values"]["advancedFabric"]["virtualIngress"]["adoption"]["exclusions"] == []

    ai_operations = documents("clusters/re8ch/components/ai-operations/platform.yaml")
    assert not any(
        item.get("metadata", {}).get("namespace") == "qwen"
        for item in ai_operations
    )


def test_headlamp_public_http_has_one_redirect_owner_and_no_gateway_loop():
    gateway_resources = documents(
        "clusters/re8ch/components/cilium/gateway-api-canary.yaml"
    )
    headlamp_origin = next(
        item
        for item in gateway_resources
        if item.get("kind") == "HTTPRoute"
        and item["metadata"]["name"] == "headlamp-edge-origin"
    )
    parents = headlamp_origin["spec"]["parentRefs"]
    assert not any(
        parent["name"] == "re8ch-gateway-canary"
        and parent.get("sectionName") == "http"
        for parent in parents
    )

    redirects = documents(
        "clusters/re8ch/components/public-exposure/legacy-special-http-routes.yaml"
    )
    headlamp_redirect = next(
        item
        for item in redirects
        if item.get("kind") == "HTTPRoute"
        and item["metadata"]["name"] == "headlamp-http-redirect"
    )
    assert headlamp_redirect["spec"]["rules"][0]["filters"][0] == {
        "type": "RequestRedirect",
        "requestRedirect": {"scheme": "https", "port": 443, "statusCode": 308},
    }

    legacy_shim = documents(
        "clusters/re8ch/components/cilium/r640-traefik-gateway-shim.yaml"
    )
    assert not any(
        item.get("metadata", {}).get("namespace") == "headlamp"
        for item in legacy_shim
    )
