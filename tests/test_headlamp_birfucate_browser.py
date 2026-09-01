from pathlib import Path
import hashlib
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "platform/plugins/headlamp-birfucate-browser"
BUNDLE = ROOT / "platform/headlamp/assets/re8ch-headlamp-birfucate-browser-0.1.0.bundle"


def test_plugin_is_explicitly_read_only():
    source = (PLUGIN / "src/index.tsx").read_text()
    assert "method: 'GET'" in source
    for mutation in ("POST", "PUT", "PATCH", "DELETE", "create(", "update(", "delete("):
        assert mutation not in source
    assert "Secret" not in source


def test_plugin_queries_birfucate_metrics_through_service_proxy():
    source = (PLUGIN / "src/index.tsx").read_text()
    assert "services/http:panel-victoria-metrics:8428/proxy" in source
    assert "birfucate_occupancy_units_total" in source
    assert "birfucate_bifurcation_score_total" in source
    assert "labels.tenant" in source
    assert "labels.resource_ref" in source


def test_bundle_is_digest_pinned_and_contains_runtime_files():
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    deployment = (ROOT / "platform/headlamp/deployment.yaml").read_text()
    assert digest == "59103b99c0dcc8520c132fc5e3e51126ee62e280cca23c9a8f4b0ff76cedc92a"
    assert digest in deployment
    with tarfile.open(BUNDLE) as archive:
        names = set(archive.getnames())
    assert "headlamp-birfucate-browser/main.js" in names
    assert "headlamp-birfucate-browser/package.json" in names


def test_headlamp_kustomization_includes_bundle():
    document = yaml.safe_load((ROOT / "platform/headlamp/kustomization.yaml").read_text())
    generators = {item["name"]: item for item in document["configMapGenerator"]}
    assert generators["headlamp-birfucate-browser-v010"]["namespace"] == "headlamp"
    assert generators["headlamp-birfucate-browser-v010"]["files"] == [
        "plugin.tar.gz=assets/re8ch-headlamp-birfucate-browser-0.1.0.bundle"
    ]
