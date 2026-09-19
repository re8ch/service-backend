from pathlib import Path
import hashlib
import re
import tarfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "platform/plugins/headlamp-birfucate-browser"
BUNDLE = ROOT / "platform/headlamp/assets/re8ch-headlamp-birfucate-browser-0.4.4.bundle"


def test_plugin_is_explicitly_read_only():
    source = (PLUGIN / "src/index.tsx").read_text() + (PLUGIN / "src/ResourceCorridors.tsx").read_text()
    assert "method: 'GET'" in source
    for mutation in ("POST", "PUT", "PATCH", "DELETE", "create(", "update(", "delete("):
        assert mutation not in source
    assert "Secret" not in source


def test_plugin_queries_birfucate_metrics_through_service_proxy():
    source = (PLUGIN / "src/index.tsx").read_text() + (PLUGIN / "src/ResourceCorridors.tsx").read_text()
    assert "from '@kinvolk/headlamp-plugin/lib/ApiProxy'" in source
    assert "withTimeout(request(path" in source
    assert "clusterRequest(path" not in source
    assert "K8s as any).clusterRequest" not in source
    assert "services/http:birfucate-metering:9791/proxy" in source
    assert "panel-victoria-metrics" not in source
    assert "birfucate_occupancy_units_total" in source
    assert "birfucate_bifurcation_score_total" in source
    assert "birfucate_metered_cost_total" in source
    assert "birfucate_tenant_cost_total" in source
    assert "birfucate_tenant_resource_count" in source
    assert "Global resource allocation" in source
    assert "ResourceCorridors" in source
    assert "owned namespace workloads" in source
    assert "Tenant API capabilities" in source
    assert "可出租上限" in source
    assert "timed out after" in source
    assert "未知值不会显示为容量 0" in source
    assert "节点来源不可读" in source
    assert "controller template" in source
    assert "volumeClaimTemplate" in source
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
        "/apis/finops.re8ch.com/v1alpha1/consumables",
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
    for field in ("Tenant contracts", "ConsumptionBinding", "quota", "startsAt"):
        assert field in source


def test_bundle_is_digest_pinned_and_contains_runtime_files():
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    deployment = (ROOT / "platform/headlamp/deployment.yaml").read_text()
    assert digest in deployment
    with tarfile.open(BUNDLE) as archive:
        names = set(archive.getnames())
        runtime_name = next(name for name in names if name.endswith("/main.js"))
        package_name = next(name for name in names if name.endswith("/package.json"))
        runtime = archive.extractfile(runtime_name).read().decode()
        package = archive.extractfile(package_name).read().decode()
    assert runtime_name
    assert package_name
    assert '"version": "0.4.4"' in package
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
    assert generators["headlamp-birfucate-browser-v044"]["namespace"] == "headlamp"
    assert generators["headlamp-birfucate-browser-v044"]["files"] == [
        "plugin.tar.gz=assets/re8ch-headlamp-birfucate-browser-0.4.4.bundle"
    ]
