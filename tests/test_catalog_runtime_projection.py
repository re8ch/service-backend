import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "catalog_runtime_project", ROOT / "operations/catalog-runtime/project.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
project = MODULE.project


def test_projection_covers_every_long_running_workload_once():
    snapshot = json.loads((ROOT / "inventory/platform/live/services.yaml").read_text())
    result = project(snapshot)
    expected = [
        item for item in snapshot["resources"]
        if item["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}
    ]
    assert len(result["items"]) == len(expected)
    assert len({item["ref"] for item in result["items"]}) == len(expected)
    assert sum(result["summary"][key] for key in ("ready", "degraded", "scaledToZero")) == len(expected)


def test_projection_only_joins_exact_same_name_service_and_ingress():
    result = project({"resources": [
        {"kind": "Deployment", "namespace": "demo", "name": "api", "desired": 1, "ready": 1},
        {"kind": "Service", "namespace": "demo", "name": "api", "type": "ClusterIP", "ports": [8080]},
        {"kind": "Ingress", "namespace": "demo", "name": "api", "hosts": ["api.example.test"]},
        {"kind": "Service", "namespace": "demo", "name": "other", "type": "ClusterIP", "ports": [80]},
    ]})
    assert result["items"] == [{
        "ref": "kubernetes/demo/deployment/api",
        "namespace": "demo",
        "name": "api",
        "kind": "Deployment",
        "desired": 1,
        "ready": 1,
        "health": "ready",
        "service": {"type": "ClusterIP", "ports": [8080]},
        "hosts": ["api.example.test"],
    }]
