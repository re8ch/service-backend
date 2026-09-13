from pathlib import Path
import hashlib
import re
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "platform/plugins/headlamp-birfucate-browser"
BUNDLE = ROOT / "platform/headlamp/assets/re8ch-headlamp-birfucate-browser-0.3.1.bundle"


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
    assert "Tenant circular resource bifurcation flow" in source
    assert "Owned namespace workloads" in source
    assert "储蓄 / 回流" in source
    assert "外部租户分流" in source
    assert source.count("Re8ch Tenant tool coverage") == 1
    assert "labels.tenant" in source
    assert "labels.resource_ref" in source


def test_plugin_renders_the_managed_tenant_contract():
    source = (PLUGIN / "src/index.tsx").read_text()
    for endpoint in (
        "/apis/tenancy.re8ch.com/v1alpha1/tenants",
        "/apis/tenancy.re8ch.com/v1alpha1/tenantidentitybindings",
        "/apis/tenancy.re8ch.com/v1alpha1/tenantgrants",
        "/apis/finops.re8ch.com/v1alpha1/clusterregistrations",
        "/apis/finops.re8ch.com/v1alpha1/clusterconnections",
        "/apis/finops.re8ch.com/v1alpha1/consumptionbindings",
        "/apis/apps/v1/deployments",
        "/apis/apps/v1/statefulsets",
        "/apis/apps/v1/daemonsets",
        "/apis/batch/v1/jobs",
        "/apis/batch/v1/cronjobs",
        "/apis/nuclio.io/v1beta1/nucliofunctions",
        "/apis/tenancy.re8ch.com/v1alpha1/tenantserviceinstances",
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
    assert '"version": "0.3.1"' in package
    assert "/apis/tenancy.re8ch.com/v1alpha1/tenants" in runtime
    assert "/apis/finops.re8ch.com/v1alpha1/clusterregistrations" in runtime


def test_complete_tenant_tool_surface_is_counted_once():
    catalog = (PLUGIN / "src/tenant-tool-catalog.ts").read_text()
    declared = yaml.safe_load((ROOT / "plugins/tenant.yaml").read_text())["spec"]["policy"]["allowedOperations"]
    grouped = re.findall(r"tools: \[(.*?)\]", catalog, re.DOTALL)
    counted = re.findall(r"'([a-z][a-z0-9-]+)'", "\n".join(grouped))
    assert len(counted) == 62
    assert len(counted) == len(set(counted))
    assert set(counted) == set(declared)


def test_headlamp_kustomization_includes_bundle():
    document = yaml.safe_load((ROOT / "platform/headlamp/kustomization.yaml").read_text())
    generators = {item["name"]: item for item in document["configMapGenerator"]}
    assert generators["headlamp-birfucate-browser-v031"]["namespace"] == "headlamp"
    assert generators["headlamp-birfucate-browser-v031"]["files"] == [
        "plugin.tar.gz=assets/re8ch-headlamp-birfucate-browser-0.3.1.bundle"
    ]
