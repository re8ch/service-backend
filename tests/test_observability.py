import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
OBSERVABILITY = ROOT / "platform" / "observability"


def yaml_documents(path: Path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def test_production_reconciliation_is_safely_suspended():
    resources = yaml_documents(ROOT / "platform" / "observability-source" / "source.yaml")
    production = next(doc for doc in resources if doc["metadata"]["name"] == "observability-production")

    assert production["spec"]["suspend"] is True
    assert production["spec"]["sourceRef"]["name"] == "service-backend"
    assert production["spec"]["path"] == "./platform/observability/production"


def test_retention_and_r640_storage_placement_are_explicit():
    resources = yaml_documents(OBSERVABILITY / "production" / "storage.yaml")
    metrics = next(doc for doc in resources if doc["kind"] == "VMCluster")
    analytics = next(doc for doc in resources if doc["kind"] == "VMSingle")
    traces = next(doc for doc in resources if doc["kind"] == "VTCluster")

    assert metrics["spec"]["retentionPeriod"] == "90d"
    assert metrics["spec"]["vmstorage"]["nodeSelector"]["kubernetes.io/hostname"] == "restricted-node-6dc3f140f9"
    assert analytics["spec"]["retentionPeriod"] == "180d"
    assert analytics["spec"]["nodeSelector"]["kubernetes.io/hostname"] == "restricted-node-6dc3f140f9"
    assert traces["spec"]["storage"]["retentionPeriod"] == "30d"
    assert traces["spec"]["storage"]["nodeSelector"]["kubernetes.io/hostname"] == "restricted-node-6dc3f140f9"


def test_vmalert_starts_in_observe_only_mode():
    resources = yaml_documents(OBSERVABILITY / "production" / "collectors.yaml")
    vmalert = next(doc for doc in resources if doc["kind"] == "VMAlert")

    assert vmalert["spec"]["extraArgs"]["notifier.blackhole"] == "true"


def test_active_recovery_vmagent_persists_advanced_fabric_measurements():
    resources = yaml_documents(OBSERVABILITY / "opencost-recovery" / "scrapes.yaml")
    scrape = next(
        doc for doc in resources
        if doc["kind"] == "VMServiceScrape"
        and doc["metadata"]["name"] == "advanced-fabric-node-measurements"
    )

    assert scrape["metadata"]["labels"]["observability.re8ch.com/opencost-recovery"] == "true"
    assert scrape["metadata"]["labels"]["observability.re8ch.com/managed"] == "true"
    assert scrape["spec"]["namespaceSelector"]["matchNames"] == ["kube-system"]
    assert scrape["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "re8ch-advanced-fabric-node-exporter"
    regex = scrape["spec"]["endpoints"][0]["metricRelabelConfigs"][0]["regex"]
    assert "tracking_ready" in regex
    assert scrape["spec"]["endpoints"][0]["port"] == "metrics"


def test_every_alert_has_ownership_and_runbook_metadata():
    documents = yaml_documents(OBSERVABILITY / "production" / "rules.yaml")
    alerts = [
        rule
        for document in documents
        for group in document["spec"]["groups"]
        for rule in group["rules"]
        if "alert" in rule
    ]

    assert alerts
    for alert in alerts:
        assert {"severity", "owner", "service", "tier", "alert_layer"} <= alert["labels"].keys()
        assert {"summary", "description", "runbook_url"} <= alert["annotations"].keys()


def test_dashboard_files_are_dense_and_have_stable_ids():
    dashboard_dir = OBSERVABILITY / "production" / "dashboards"
    dashboards = [json.loads(path.read_text()) for path in sorted(dashboard_dir.glob("*.json"))]

    assert len(dashboards) == 6
    assert len({dashboard["uid"] for dashboard in dashboards}) == 6
    for dashboard in dashboards:
        assert dashboard["title"].startswith("RE8CH ")
        assert len(dashboard["panels"]) >= 8


def test_service_catalog_covers_nodes_and_service_classes():
    catalog = yaml.safe_load((ROOT / "inventory" / "observability-services.yaml").read_text())
    spec = catalog["spec"]

    assert len(spec["nodes"]) >= 13
    assert len(spec["serviceGroups"]) >= 20
    assert {"storage", "network", "stateful", "stateless", "function"} <= {
        group["workloadClass"] for group in spec["serviceGroups"]
    }
    assert spec["scrapeProfiles"] == {"critical": "15s", "standard": "30s", "heavy": "60s"}
