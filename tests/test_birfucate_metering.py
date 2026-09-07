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


def test_default_meters_cover_ceph_pigsty_hubble_hyperglass_and_opencost():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["opencost"]["enabled"] is True
    assert values["prometheus"]["writePath"] == "/api/v1/import/prometheus"
    assert values["vmServiceScrape"]["enabled"] is False
    domains = {item["domain"] for family in values["meters"].values() for item in family}
    names = {item["name"] for family in values["meters"].values() for item in family}
    assert domains == {"compute", "ceph", "pigsty", "networks"}
    assert {"tenant-claim-cpu-request", "tenant-claim-memory-request", "tenant-claim-ephemeral-storage-request"} <= names
    assert {"ceph-client-io", "pigsty-transactions", "hubble-network-flows", "hyperglass-route-branches"} <= names


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
