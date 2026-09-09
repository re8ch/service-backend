from pathlib import Path
import hashlib
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "platform/plugins/headlamp-birfucate-browser"
BUNDLE = ROOT / "platform/headlamp/assets/re8ch-headlamp-birfucate-browser-0.2.0.bundle"


def test_plugin_is_explicitly_read_only():
    source = (PLUGIN / "src/index.tsx").read_text()
    assert "method: 'GET'" in source
    for mutation in ("POST", "PUT", "PATCH", "DELETE", "create(", "update(", "delete("):
        assert mutation not in source
    assert "Secret" not in source


def test_plugin_queries_birfucate_metrics_through_service_proxy():
    source = (PLUGIN / "src/index.tsx").read_text()
    assert "from '@kinvolk/headlamp-plugin/lib/ApiProxy'" in source
    assert "await request(path" in source
    assert "clusterRequest(path" not in source
    assert "K8s as any).clusterRequest" not in source
    assert "services/http:birfucate-metering:9791/proxy" in source
    assert "panel-victoria-metrics" not in source
    assert "birfucate_occupancy_units_total" in source
    assert "birfucate_bifurcation_score_total" in source
    assert "birfucate_metered_cost_total" in source
    assert "birfucate_tenant_cost_total" in source
    assert "birfucate_tenant_resource_count" in source
    assert "Tenant → Resource type → Purpose" in source
    assert "labels.tenant" in source
    assert "labels.resource_ref" in source


def test_plugin_renders_the_managed_tenant_contract():
    source = (PLUGIN / "src/index.tsx").read_text()
    for endpoint in (
        "/apis/tenancy.re8ch.com/v1alpha1/tenants",
        "/apis/tenancy.re8ch.com/v1alpha1/tenantidentitybindings",
        "/apis/tenancy.re8ch.com/v1alpha1/tenantgrants",
        "/apis/finops.re8ch.com/v1alpha1/clusterregistrations",
        "/api/v1/namespaces",
    ):
        assert endpoint in source
    for field in ("hostingMode", "byocAllowed", "businessNamespaces", "OIDC identities"):
        assert field in source


def test_bundle_is_digest_pinned_and_contains_runtime_files():
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    deployment = (ROOT / "platform/headlamp/deployment.yaml").read_text()
    assert digest in deployment
    with tarfile.open(BUNDLE) as archive:
        names = set(archive.getnames())
        runtime = archive.extractfile("headlamp-birfucate-browser/main.js").read().decode()
        package = archive.extractfile("headlamp-birfucate-browser/package.json").read().decode()
    assert "headlamp-birfucate-browser/main.js" in names
    assert "headlamp-birfucate-browser/package.json" in names
    assert '"version": "0.2.0"' in package
    assert "/apis/tenancy.re8ch.com/v1alpha1/tenants" in runtime
    assert "/apis/finops.re8ch.com/v1alpha1/clusterregistrations" in runtime


def test_headlamp_kustomization_includes_bundle():
    document = yaml.safe_load((ROOT / "platform/headlamp/kustomization.yaml").read_text())
    generators = {item["name"]: item for item in document["configMapGenerator"]}
    assert generators["headlamp-birfucate-browser-v020"]["namespace"] == "headlamp"
    assert generators["headlamp-birfucate-browser-v020"]["files"] == [
        "plugin.tar.gz=assets/re8ch-headlamp-birfucate-browser-0.2.0.bundle"
    ]
