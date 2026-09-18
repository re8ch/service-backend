import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
OBSERVABILITY = ROOT / "platform" / "observability"


def yaml_documents(path: Path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def test_victoria_metrics_operator_webhook_uses_routable_ha_nodes():
    resources = yaml_documents(OBSERVABILITY / "bootstrap" / "victoria-metrics-operator.yaml")
    release = next(doc for doc in resources if doc["kind"] == "HelmRelease")
    values = release["spec"]["values"]

    node_expression = values["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert node_expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["restricted-node-6dc3f140f9", "restricted-node-1eb0570f74"],
    }
    assert values["tolerations"] == [
        {
            "key": "node-role.kubernetes.io/control-plane",
            "operator": "Equal",
            "value": "true",
            "effect": "NoSchedule",
        }
    ]
    patch = release["spec"]["postRenderers"][0]["kustomize"]["patches"][0]
    assert patch["target"] == {
        "group": "apps",
        "version": "v1",
        "kind": "Deployment",
        "name": "victoria-metrics-operator",
    }
    patched_deployment = yaml.safe_load(patch["patch"])
    assert patched_deployment["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {
            "name": "KUBERNETES_SERVICE_HOST",
            "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}},
        },
        {"name": "KUBERNETES_SERVICE_PORT", "value": "6443"},
    ]


def test_production_reconciliation_is_enabled_from_verified_oci_source():
    resources = yaml_documents(ROOT / "platform" / "observability-source" / "source.yaml")
    production = next(doc for doc in resources if doc["metadata"]["name"] == "observability-production")

    assert production["spec"]["suspend"] is False
    source = yaml_documents(ROOT / "clusters/re8ch/domain-sources/platform-observability-production.yaml")[0]
    assert production["spec"]["sourceRef"] == {"kind": "OCIRepository", "name": source["metadata"]["name"]}
    assert production["spec"]["path"] == "./"


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


def test_vmalert_uses_ha_alertmanager_notifier():
    resources = yaml_documents(OBSERVABILITY / "production" / "collectors.yaml")
    vmalert = next(doc for doc in resources if doc["kind"] == "VMAlert")

    assert "extraArgs" not in vmalert["spec"] or "notifier.blackhole" not in vmalert["spec"]["extraArgs"]
    selector = vmalert["spec"]["notifiers"][0]["selector"]
    assert selector["namespaceSelector"]["matchNames"] == ["observability-system"]
    assert selector["labelSelector"]["matchLabels"]["usage"] == "re8ch-platform-alerting"


def test_logs_and_alertmanager_are_declared_but_production_remains_gated():
    logs = yaml_documents(OBSERVABILITY / "production" / "logs.yaml")
    vlsingle = next(doc for doc in logs if doc["kind"] == "VLSingle")
    vlagent = next(doc for doc in logs if doc["kind"] == "VLAgent")
    alerting = yaml_documents(OBSERVABILITY / "production" / "alertmanager.yaml")
    alertmanager = next(doc for doc in alerting if doc["kind"] == "VMAlertmanager")

    assert vlsingle["spec"]["retentionPeriod"] == "14d"
    assert vlsingle["spec"]["storage"]["storageClassName"] == "rbd-r640"
    assert vlagent["spec"]["k8sCollector"]["enabled"] is True
    assert alertmanager["spec"]["replicaCount"] == 3
    assert alertmanager["spec"]["configNamespaceSelector"] == {}


def test_alertmanager_routes_each_severity_to_incidentrelay_with_secret_tokens():
    alerting = yaml_documents(OBSERVABILITY / "production" / "alertmanager.yaml")
    config = next(doc for doc in alerting if doc["kind"] == "Secret")
    alertmanager = next(doc for doc in alerting if doc["kind"] == "VMAlertmanager")
    rendered = config["stringData"]["alertmanager.yaml"]

    assert alertmanager["spec"]["secrets"] == ["incidentrelay-alertmanager-tokens"]
    for severity in ("critical", "warning", "info"):
        assert f"incidentrelay-{severity}" in rendered
        assert (
            "/etc/vm/secrets/incidentrelay-alertmanager-tokens/" + severity
        ) in rendered
    assert rendered.count("http_config:") == 3
    assert "incidentrelay.incidentrelay.svc.cluster.local:8080" in rendered


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
    assert scrape["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "advanced-fabric-node-exporter"
    regex = scrape["spec"]["endpoints"][0]["metricRelabelConfigs"][0]["regex"]
    assert "tracking_ready" in regex
    assert scrape["spec"]["endpoints"][0]["port"] == "metrics"


def test_active_metrics_scrape_gateway_receive_bytes_per_node():
    resources = yaml_documents(OBSERVABILITY / "opencost-recovery" / "scrapes.yaml")
    scrape = next(
        doc for doc in resources
        if doc["kind"] == "VMPodScrape"
        and doc["metadata"]["name"] == "cilium-envoy-gateway"
    )

    assert scrape["metadata"]["labels"] == {
        "observability.re8ch.com/managed": "true",
        "observability.re8ch.com/opencost-recovery": "true",
    }
    assert scrape["spec"]["namespaceSelector"]["matchNames"] == ["kube-system"]
    assert scrape["spec"]["selector"]["matchLabels"]["k8s-app"] == "cilium-envoy"
    endpoint = scrape["spec"]["podMetricsEndpoints"][0]
    assert endpoint["targetPort"] == 9964
    assert endpoint["path"] == "/metrics"
    assert {
        "sourceLabels": ["__meta_kubernetes_pod_node_name"],
        "targetLabel": "node",
    } in endpoint["relabelConfigs"]
    metric_filter = endpoint["metricRelabelConfigs"][0]
    assert metric_filter["action"] == "keep"
    assert "listener_downstream_cx_rx_bytes_total" in metric_filter["regex"]
    assert "http_downstream_cx_rx_bytes_total" in metric_filter["regex"]
    assert "tcp_.+_downstream_cx_rx_bytes_total" in metric_filter["regex"]
    assert "upstream" not in metric_filter["regex"]
    assert "buffered" not in metric_filter["regex"]

    runtime = yaml_documents(OBSERVABILITY / "opencost-recovery" / "runtime.yaml")
    vmagent = next(doc for doc in runtime if doc["kind"] == "VMAgent")
    assert vmagent["spec"]["podScrapeSelector"]["matchLabels"] == {
        "observability.re8ch.com/opencost-recovery": "true",
    }


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
        assert {"severity", "owner", "service", "tier", "alert_layer", "policy_id"} <= alert["labels"].keys()
        assert {"summary", "description", "runbook_url"} <= alert["annotations"].keys()
        assert "gitlab.com" not in alert["annotations"]["runbook_url"]


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
