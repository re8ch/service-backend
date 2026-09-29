import importlib.util
import json
import math
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
CHART = ROOT / "clusters/re8ch/charts/birfucate-metering"
SPEC = importlib.util.spec_from_file_location("birfucate_collector", CHART / "files/collector.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def test_production_collector_uses_stable_cached_runtime():
    release = yaml.safe_load((ROOT / "platform/birfucate-metering/release.yaml").read_text())
    values = release["spec"]["values"]

    assert values["image"]["tag"] == (
        "3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d"
    )
    assert values["nodeSelector"] == {"kubernetes.io/hostname": "restricted-node-6dc3f140f9"}
    re8ch = next(item for item in values["tenantPlatform"]["tenants"] if item["name"] == "re8ch")
    grants = {item["consumableRef"] for item in re8ch["grants"]}
    assert {"ai.model-gateway.v1", "sandbox.runtime.v1"} <= grants


def test_chart_lints_and_renders_restricted_workload():
    subprocess.run(["helm", "lint", str(CHART)], check=True, capture_output=True, text=True)
    rendered = subprocess.run(["helm", "template", "birfucate", str(CHART), "--namespace", "observability-ai", "--kube-version", "1.35.0", "--include-crds"], check=True, capture_output=True, text=True).stdout
    docs = [item for item in yaml.safe_load_all(rendered) if item]
    deployment = next(item for item in docs if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    annotations = deployment["spec"]["template"]["metadata"]["annotations"]
    assert pod["automountServiceAccountToken"] is False
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert "IMAGE_REQUIRES_VERIFIED_DIGEST" in container["image"]
    assert container["command"] == ["python3", "/etc/birfucate/collector.py"]
    assert len(annotations["checksum/collector"]) == 64
    assert len(annotations["checksum/config"]) == 64
    discovery = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"].endswith("-discovery"))
    assert discovery["spec"]["template"]["spec"]["automountServiceAccountToken"] is True
    role = next(item for item in docs if item["kind"] == "ClusterRole")
    writable = next(rule for rule in role["rules"] if rule["apiGroups"] == ["finops.re8ch.com"] and "serviceshards" in rule["resources"])
    assert "create" in writable["verbs"]
    assert not any("secrets" in rule["resources"] for rule in role["rules"])


def test_usage_sink_renders_projected_identity_and_private_receiver():
    rendered = subprocess.run(
        ["helm", "template", "birfucate", str(CHART), "--namespace", "observability-ai", "--set", "tenantPlatform.enabled=true", "--set", "usageSink.enabled=true"],
        check=True, capture_output=True, text=True,
    ).stdout
    docs = [item for item in yaml.safe_load_all(rendered) if item]
    collector = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] == "birfucate-metering")
    projected = next(item for item in collector["spec"]["template"]["spec"]["volumes"] if item["name"] == "usage-token")
    assert projected["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "re8ch-usage"
    tenant_api = next(item for item in docs if item["kind"] == "Deployment" and item["metadata"]["name"] == "tenant-api")
    env = {item["name"]: item["value"] for item in tenant_api["spec"]["template"]["spec"]["containers"][0]["env"] if "value" in item}
    assert env["TENANT_USAGE_CLIENT"] == "system:serviceaccount:observability-ai:birfucate-metering"
    assert any(item["kind"] == "Service" and item["metadata"]["name"] == "tenant-infrastructure" for item in docs)
    role = next(item for item in docs if item["kind"] == "ClusterRole" and item["metadata"]["name"] == "tenant-usage-token-reviewer")
    assert role["rules"] == [{"apiGroups": ["authentication.k8s.io"], "resources": ["tokenreviews"], "verbs": ["create"]}]


def test_default_meters_cover_infrastructure_ai_gateway_and_sandbox():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["opencost"]["enabled"] is True
    assert values["prometheus"]["baseURL"] == "http://vmsingle-opencost.observability-system.svc.cluster.local:8428"
    assert values["prometheus"]["writePath"] == "/api/v1/import/prometheus"
    assert values["vmServiceScrape"]["enabled"] is False
    domains = {item["domain"] for family in values["meters"].values() for item in family}
    names = {item["name"] for family in values["meters"].values() for item in family}
    assert domains == {"compute", "ceph", "pigsty", "networks", "ai-gateway", "sandbox"}
    assert {"tenant-claim-cpu-request", "tenant-claim-memory-request", "tenant-claim-ephemeral-storage-request"} <= names
    assert {"tenant-workload-cpu-used", "tenant-workload-memory-used", "ceph-volume-requested", "ceph-volume-capacity", "ceph-volume-used"} <= names
    assert {"ceph-client-io", "pigsty-transactions", "hubble-network-flows", "hyperglass-route-branches"} <= names
    assert {"ai-gateway-provider-spend", "ai-gateway-tool-spend", "ai-gateway-tool-calls"} <= names
    assert {"sandbox-cpu-request", "sandbox-memory-request"} <= names


def test_namespace_meter_uses_controller_ownership_and_excludes_individually_metered_pods(tmp_path, monkeypatch):
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    ksm = next(item for item in yaml.safe_load_all((ROOT / "platform/observability/bootstrap/kube-state-metrics.yaml").read_text()) if item["kind"] == "HelmRelease")
    allowlist = ksm["spec"]["values"]["metricLabelsAllowlist"]
    assert "namespaces=[tenancy.re8ch.com/tenant]" in allowlist
    assert any("birfucate.re8ch.com/metered" in item for item in allowlist if item.startswith("pods="))
    meters = [item for item in values["meters"]["occupancy"] if item["name"].startswith("tenant-namespace-")]
    assert {item["name"] for item in meters} == {"tenant-namespace-cpu-request", "tenant-namespace-memory-request"}
    assert all('kube_namespace_labels{label_tenancy_re8ch_com_tenant!=""}' in item["query"] for item in meters)
    assert all('unless on (exported_namespace, exported_pod) kube_pod_labels{label_birfucate_re8ch_com_metered="true"}' in item["query"] for item in meters)
    assert all('label_replace(' in item["query"] and '"namespace", "$1", "exported_namespace"' in item["query"] for item in meters)
    config = {"prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 1}, "opencost": {"enabled": False},
              "tenantLabels": values["tenantLabels"], "meters": {"occupancy": meters}}
    monkeypatch.setattr(collector, "prometheus_query", lambda _base, query, _timeout: [{"metric": {
        "label_tenancy_re8ch_com_tenant": "re8ch", "namespace": "re8ch-default"}, "value": [0, "2"]}] if 'resource="cpu"' in query else [])
    outbox = collector.Outbox(tmp_path / "outbox.db")
    collector.collect_once(config, outbox, 100, 160)
    event = outbox.pending()[0][1]
    assert (event["tenantRef"], event["resourceRef"], event["serviceClass"]) == ("re8ch", "re8ch-default", "compute.workload.v1")
    assert (event["quantity"], event["unit"], event["pricingApplied"]) == (120, "core-second", False)


def test_usage_outbox_can_limit_events_to_managed_tenant(tmp_path, monkeypatch):
    config = {
        "prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 1},
        "opencost": {"enabled": False}, "usageSink": {"allowedTenants": ["re8ch"]},
        "tenantLabels": {"tenant": "tenant", "resource": "resource_ref"},
        "meters": {"occupancy": [{"name": "cpu", "domain": "compute", "unit": "core-second", "query": "cpu"}]},
    }
    monkeypatch.setattr(collector, "prometheus_query", lambda *_args: [
        {"metric": {"tenant": "re8ch", "resource_ref": "work-a"}, "value": [0, "1"]},
        {"metric": {"tenant": "artchais", "resource_ref": "work-b"}, "value": [0, "2"]},
    ])
    outbox = collector.Outbox(tmp_path / "outbox.db")
    collector.collect_once(config, outbox, 100, 160)
    assert [event["tenantRef"] for _, event in outbox.pending()] == ["re8ch"]


def test_crds_use_status_subresources_cel_and_selectable_fields():
    rendered = subprocess.run(["helm", "template", "birfucate", str(CHART), "--namespace", "observability-ai", "--kube-version", "1.35.0", "--include-crds"], check=True, capture_output=True, text=True).stdout
    crds = {item["spec"]["names"]["kind"]: item for item in yaml.safe_load_all(rendered) if item and item["kind"] == "CustomResourceDefinition" and item["spec"]["group"] == "finops.re8ch.com"}
    assert set(crds) == {"Consumable", "ServiceShard", "ConsumptionBinding", "BifurcationPath", "Tenant", "ClusterRegistration", "ClusterConnection"}
    for crd in crds.values():
        version = crd["spec"]["versions"][0]
        assert version["subresources"] == {"status": {}}
        assert version["selectableFields"]
        assert version["additionalPrinterColumns"]
    consumable_spec = crds["Consumable"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    assert consumable_spec["x-kubernetes-validations"]
    assert not any(path.name.endswith("crds.yaml") for path in (CHART / "templates").iterdir())
    assert {path.name for path in (CHART / "crds").iterdir()} == {"finops.re8ch.com.yaml", "tenancy.re8ch.com.yaml"}


def test_bifurcation_formula_preserves_occurrence_intensity_fanout(tmp_path, monkeypatch):
    config = {
        "prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 1},
        "opencost": {"enabled": False},
        "tenantLabels": {"tenant": "tenant", "resource": "resource_ref"},
        "meters": {
            "occupancy": [{"name": "capacity", "domain": "ceph", "unit": "GiB-second", "valueKind": "gauge", "query": "occupancy"}],
            "bifurcation": [{"name": "io", "domain": "ceph", "occurrenceUnit": "operation", "intensityUnit": "byte-per-operation", "referenceIntensity": 4, "countQuery": "count", "intensityQuery": "intensity", "fanoutQuery": "fanout"}],
        },
    }
    data = {
        "occupancy": [{"metric": {"tenant": "qwen", "resource_ref": "service://qwen/vol"}, "value": [0, "2"]}],
        "count": [{"metric": {"tenant": "qwen", "resource_ref": "service://qwen/vol"}, "value": [0, "3"]}],
        "intensity": [{"metric": {"tenant": "qwen", "resource_ref": "service://qwen/vol"}, "value": [0, "12"]}],
        "fanout": [{"metric": {"tenant": "qwen", "resource_ref": "service://qwen/vol"}, "value": [0, "2"]}],
    }
    monkeypatch.setattr(collector, "prometheus_query", lambda base, query, timeout: data[query])
    collector.METRICS.clear()
    outbox = collector.Outbox(tmp_path / "outbox.db")
    collector.collect_once(config, outbox, 100, 110)
    events = [event for _, event in outbox.pending()]
    occupancy = next(event for event in events if event["meter"] == "occupancy.capacity")
    behavior = next(event for event in events if event["meter"] == "bifurcation.io")
    assert occupancy["quantity"] == 20
    assert occupancy["pricingApplied"] is False
    assert occupancy["accountingPosted"] is False
    assert behavior["dimensions"]["occurrences"] == 30
    assert behavior["dimensions"]["intensity"] == 12
    assert behavior["dimensions"]["fanout"] == 2
    assert behavior["quantity"] == 30 * math.log2(1 + 12 / 4) * 2


def test_vectors_without_tenant_and_resource_are_dropped():
    items = [
        {"metric": {"tenant": "qwen"}, "value": [0, "1"]},
        {"metric": {"resource_ref": "service://qwen/x"}, "value": [0, "2"]},
        {"metric": {"tenant": "qwen", "resource_ref": "service://qwen/x"}, "value": [0, "3"]},
    ]
    assert collector.vector(items, "tenant", "resource_ref") == {("qwen", "service://qwen/x"): 3}


def test_showback_pricing_is_explicit_and_never_posts_accounting(tmp_path, monkeypatch):
    config = {
        "prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 1},
        "opencost": {"enabled": False}, "pricing": {"enabled": True, "currency": "CNY"},
        "tenantLabels": {"tenant": "tenant", "resource": "resource_ref"},
        "meters": {"occupancy": [{"name": "cpu", "domain": "compute", "resourceType": "cpu", "purpose": "runtime", "unit": "core-second", "valueKind": "gauge", "pricePerBillingUnit": 0.2, "billingUnitSize": 3600, "query": "cpu"}], "bifurcation": []},
    }
    monkeypatch.setattr(collector, "prometheus_query", lambda *_: [{"metric": {"tenant": "acme", "resource_ref": "pod/a"}, "value": [0, "2"]}])
    collector.METRICS.clear()
    outbox = collector.Outbox(tmp_path / "outbox.db")
    collector.collect_once(config, outbox, 0, 3600)
    event = outbox.pending()[0][1]
    assert event["pricingApplied"] is True
    assert event["accountingPosted"] is False
    assert event["cost"] == pytest.approx(0.4)
    rendered = collector.render_metrics().decode()
    assert 'birfucate_metered_cost_total{currency="CNY"' in rendered
    assert 'purpose="runtime"' in rendered
    assert 'birfucate_tenant_cost_total{currency="CNY",tenant="acme"} 0.4' in rendered


def test_direct_usage_preserves_gateway_spend_and_service_class(tmp_path, monkeypatch):
    config = {
        "prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 1},
        "opencost": {"enabled": False}, "pricing": {"enabled": True, "currency": "CNY"},
        "tenantLabels": {"tenant": "tenant", "resource": "resource_ref"},
        "meters": {"occupancy": [], "bifurcation": [], "usage": [{
            "name": "provider-spend", "domain": "ai-gateway", "serviceClass": "ai.model-gateway.v1",
            "resourceType": "model-spend", "purpose": "model-inference", "unit": "USD",
            "tenantLabel": "team_alias", "resourceLabel": "requested_model", "query": "spend",
            "costFromQuantity": True, "currency": "USD",
        }]},
    }
    monkeypatch.setattr(collector, "prometheus_query", lambda *_: [{"metric": {"team_alias": "re8ch", "requested_model": "gpt-5"}, "value": [0, "1.25"]}])
    collector.METRICS.clear()
    outbox = collector.Outbox(tmp_path / "outbox.db")
    collector.collect_once(config, outbox, 0, 300)
    event = outbox.pending()[0][1]
    assert event["serviceClass"] == "ai.model-gateway.v1"
    assert event["quantity"] == 1.25
    assert event["cost"] == 1.25
    assert event["currency"] == "USD"
    assert event["pricingApplied"] is True


def test_outbox_delivery_is_idempotent(tmp_path):
    outbox = collector.Outbox(tmp_path / "outbox.db")
    event = {"eventId": "ue_" + "a" * 32}
    outbox.put(event); outbox.put(event)
    assert len(outbox.pending()) == 1


def test_remote_write_render_includes_explicit_millisecond_timestamp():
    collector.METRICS.clear()
    collector.READY = True
    collector.set_metric("birfucate_dependency_up", {"dependency": "prometheus"}, 1)
    local = collector.render_metrics().decode()
    remote = collector.render_metrics(1234567890000).decode()
    assert "birfucate_up 1\n" in local
    assert "birfucate_up 1 1234567890000\n" in remote
    assert 'birfucate_dependency_up{dependency="prometheus"} 1.0 1234567890000' in remote


def test_browser_query_is_chart_configured_and_metric_allowlisted(monkeypatch):
    config = {
        "browserAPI": {"enabled": True, "lookbackSeconds": 600},
        "prometheus": {"baseURL": "http://metrics", "timeoutSeconds": 3},
    }
    captured = {}

    def query(base, expression, timeout):
        captured.update(base=base, expression=expression, timeout=timeout)
        return [{"metric": {"tenant": "acme", "resource_ref": "claim-1"}, "value": [0, "2"]}]

    monkeypatch.setattr(collector, "prometheus_query", query)
    payload = collector.browser_query(config, "birfucate_occupancy_current")
    assert payload["status"] == "success"
    assert captured == {
        "base": "http://metrics",
        "expression": "last_over_time(birfucate_occupancy_current[600s])",
        "timeout": 3,
    }
    with pytest.raises(ValueError, match="metric_not_allowed"):
        collector.browser_query(config, "up")
