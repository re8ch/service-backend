import importlib.util
import pathlib
from unittest import mock


PATH = pathlib.Path(__file__).parents[1] / "clusters/re8ch/charts/runtime-adapter/files/gateway.py"
SPEC = importlib.util.spec_from_file_location("runtime_adapter_gateway", PATH)
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


def test_routes_workload_to_matching_provider():
    providers = [{
        "name": "spin", "kind": "spin", "enabled": True,
        "workloadPrefixes": ["wasm/"], "pathPrefix": "/", "priority": 100,
    }]
    with mock.patch.object(GATEWAY, "load_providers", return_value=providers), \
         mock.patch.object(GATEWAY, "endpoints", return_value=[("http://192.0.2.132:8080", "edge", 100)]):
        result = GATEWAY.candidates("wasm/hello")
    assert result[0][3] == "http://192.0.2.132:8080/hello"
    assert result[0][2]["kind"] == "spin"


def test_advanced_fabric_capacity_ranks_endpoints():
    GATEWAY.CACHE["endpoints"].clear()
    provider = {"name": "local", "service": {"name": "runtime", "namespace": "runtime-adapter", "port": 8080}}
    slices = {"items": [{"ports": [{"port": 8080}], "endpoints": [
        {"addresses": ["192.0.2.103"], "nodeName": "small", "conditions": {"ready": True}},
        {"addresses": ["192.0.2.131"], "nodeName": "restricted-node-6dc3f140f9", "conditions": {"ready": True}},
    ]}]}
    with mock.patch.object(GATEWAY, "api_get", return_value=slices), \
         mock.patch.object(GATEWAY, "fabric_capacity", return_value={"small": 100, "restricted-node-6dc3f140f9": 1000}):
        result = GATEWAY.endpoints(provider)
    assert result[0] == ("http://192.0.2.131:8080", "restricted-node-6dc3f140f9", 1000)


def test_external_provider_requires_https():
    assert GATEWAY.endpoints({"url": "http://unsafe.example"}) == []
    assert GATEWAY.endpoints({"url": "https://safe.example"}) == [("https://safe.example", "external", 1.0)]


def test_unready_endpoints_are_excluded():
    GATEWAY.CACHE["endpoints"].clear()
    provider = {"name": "local", "service": {"name": "runtime", "namespace": "runtime-adapter", "port": 8080}}
    slices = {"items": [{"ports": [{"port": 8080}], "endpoints": [
        {"addresses": ["192.0.2.103"], "nodeName": "edge", "conditions": {"ready": False}},
    ]}]}
    with mock.patch.object(GATEWAY, "api_get", return_value=slices), \
         mock.patch.object(GATEWAY, "fabric_capacity", return_value={}):
        result = GATEWAY.endpoints(provider)
    assert result == [("http://runtime.runtime-adapter.svc.cluster.local:8080", "service", 1.0)]
