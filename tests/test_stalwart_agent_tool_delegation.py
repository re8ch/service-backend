import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def collaboration_service():
    services = json.loads(
        (ROOT / "platform/apps/tenant-mcp/service_catalog.json").read_text()
    )
    return next(
        service
        for service in services
        if service["serviceClass"] == "collaboration.stalwart.v1"
    )


def test_stalwart_agent_tool_source_and_delivery_are_separate():
    provider = collaboration_service()["toolProvider"]
    assert provider == {
        "contract": "stalwart-agent-tools.re8ch.com/v1alpha1",
        "deliveryRepository": "https://github.com/8iops/agent-gitops",
        "runtime": "toolhive-mcpserver",
        "sourceRepository": "https://github.com/8iops/stalwart-agent-tools",
        "transport": "public-edge",
    }
    assert provider["sourceRepository"] != provider["deliveryRepository"]


def test_service_catalog_does_not_copy_provider_api_or_dynamic_state():
    provider = collaboration_service()["toolProvider"]
    forbidden = {
        "connectionRef",
        "credentials",
        "jmapMethods",
        "managementSchema",
        "providerToken",
        "serviceInstances",
        "userBindings",
    }
    assert forbidden.isdisjoint(provider)
