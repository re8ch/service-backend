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
