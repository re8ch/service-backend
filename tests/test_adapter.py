import importlib.util
import pathlib
from unittest import mock


PATH = pathlib.Path(__file__).parents[1] / "clusters/re8ch/charts/adapter/files/gateway.py"
SPEC = importlib.util.spec_from_file_location("adapter_gateway", PATH)
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


def test_routes_workload_to_matching_provider():
    providers = [{
        "name": "spin", "kind": "spin", "enabled": True,
        "workloadPrefixes": ["wasm/"], "pathPrefix": "/", "priority": 100,
    }]
    with mock.patch.object(GATEWAY, "load_providers", return_value=providers), \
         mock.patch.object(GATEWAY, "endpoints", return_value=[("http://10.0.0.2:8080", "edge", 100)]):
        result = GATEWAY.candidates("wasm/hello")
    assert result[0][3] == "http://10.0.0.2:8080/hello"
    assert result[0][2]["kind"] == "spin"


def test_advanced_fabric_capacity_ranks_endpoints():
    GATEWAY.CACHE["endpoints"].clear()
    provider = {"name": "local", "service": {"name": "runtime", "namespace": "adapter", "port": 8080}}
    slices = {"items": [{"ports": [{"port": 8080}], "endpoints": [
        {"addresses": ["10.42.0.2"], "nodeName": "small", "conditions": {"ready": True}},
        {"addresses": ["10.42.0.3"], "nodeName": "r640", "conditions": {"ready": True}},
    ]}]}
    with mock.patch.object(GATEWAY, "api_get", return_value=slices), \
         mock.patch.object(GATEWAY, "fabric_capacity", return_value={"small": 100, "r640": 1000}):
        result = GATEWAY.endpoints(provider)
    assert result[0] == ("http://10.42.0.3:8080", "r640", 1000)


def test_external_provider_requires_https():
    assert GATEWAY.endpoints({"url": "http://unsafe.example"}) == []
    assert GATEWAY.endpoints({"url": "https://safe.example"}) == [("https://safe.example", "external", 1.0)]


def test_unready_endpoints_are_excluded():
    GATEWAY.CACHE["endpoints"].clear()
    provider = {"name": "local", "service": {"name": "runtime", "namespace": "adapter", "port": 8080}}
    slices = {"items": [{"ports": [{"port": 8080}], "endpoints": [
        {"addresses": ["10.42.0.2"], "nodeName": "edge", "conditions": {"ready": False}},
    ]}]}
    with mock.patch.object(GATEWAY, "api_get", return_value=slices), \
         mock.patch.object(GATEWAY, "fabric_capacity", return_value={}):
        result = GATEWAY.endpoints(provider)
    assert result == []


def test_nuclio_discovers_ready_function_and_endpoint_slice():
    GATEWAY.CACHE["nuclio"].clear()
    provider = {
        "name": "nuclio", "kind": "nuclio", "enabled": True,
        "workloadPrefixes": ["nuclio/"], "priority": 110,
        "discovery": {"namespaces": ["qwen"], "includeScaledToZero": True},
    }
    function = {"status": {"state": "ready", "internalInvocationUrls": [
        "nuclio-workflow-api.qwen.svc.cluster.local:8080"
    ]}}
    with mock.patch.object(GATEWAY, "load_providers", return_value=[provider]), \
         mock.patch.object(GATEWAY, "nuclio_function", return_value=function), \
         mock.patch.object(GATEWAY, "nuclio_services", return_value=[]), \
         mock.patch.object(GATEWAY, "endpoints", return_value=[("http://10.42.12.8:8080", "qwen-1", 3)]):
        result = GATEWAY.candidates("nuclio/qwen/workflow-api/v1/jobs")
    assert result[0][3] == "http://10.42.12.8:8080/v1/jobs"
    assert result[0][2]["nuclioFunction"] == "qwen/workflow-api"


def test_nuclio_rejects_unhealthy_or_unapproved_namespace():
    provider = {
        "name": "nuclio", "kind": "nuclio", "workloadPrefixes": ["nuclio/"],
        "discovery": {"namespaces": ["ledger"]},
    }
    assert GATEWAY.nuclio_candidates("nuclio/qwen/api/", provider, "nuclio/") == []
    with mock.patch.object(GATEWAY, "nuclio_function", return_value={"status": {"state": "unhealthy"}}):
        assert GATEWAY.nuclio_candidates("nuclio/ledger/api/", provider, "nuclio/") == []


def test_nuclio_discovers_existing_selector_service_without_cr():
    GATEWAY.CACHE["nuclio_services"].clear()
    provider = {"discovery": {"cacheSeconds": 5}}
    services = {"items": [{
        "metadata": {"name": "ledger-api-local", "namespace": "ledger"},
        "spec": {"selector": {"nuclio.io/function-name": "ledger-api"}, "ports": [{"port": 8080}]},
    }, {
        "metadata": {"name": "unrelated"},
        "spec": {"selector": {"app": "other"}, "ports": [{"port": 80}]},
    }]}
    with mock.patch.object(GATEWAY, "api_get", return_value=services):
        assert GATEWAY.nuclio_services(provider, "ledger", "ledger-api") == [
            {"name": "ledger-api-local", "namespace": "ledger", "port": 8080}
        ]


def test_runtime_observations_are_exported_with_nuclio_labels():
    GATEWAY.PROVIDER_COUNTERS.clear()
    GATEWAY.ENDPOINT_STATE.clear()
    provider = {"name": "nuclio", "kind": "nuclio", "nuclioFunction": "qwen/api"}
    GATEWAY.observe(provider, "200", 12.5, "http://10.42.1.2:8080/path")
    metrics = GATEWAY.prometheus_metrics().decode()
    assert 'provider="nuclio",kind="nuclio",function="qwen/api",outcome="200"' in metrics
    assert "re8ch_adapter_endpoint_ewma_milliseconds" in metrics
