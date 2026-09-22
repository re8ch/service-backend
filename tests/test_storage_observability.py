from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def docs(path):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def test_node_storage_exporter_is_bounded_and_read_only():
    resources = docs("platform/observability/production/node-storage-exporter.yaml")
    daemonset = next(item for item in resources if item["kind"] == "DaemonSet")
    pod = daemonset["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert not pod.get("hostNetwork", False)
    assert not pod.get("hostPID", False)
    assert not any(toleration.get("operator") == "Exists" and not toleration.get("key")
                   for toleration in pod.get("tolerations", []))
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert not container["securityContext"]["allowPrivilegeEscalation"]
    assert container["resources"]["requests"] == {"cpu": "10m", "memory": "12Mi"}
    assert "--collector.disable-defaults" in container["args"]
    assert all(mount["readOnly"] for mount in container["volumeMounts"])


def test_storage_scrape_and_alerts_use_ready_nodes_only():
    scrape = next(item for item in docs("platform/observability/production/scrapes.yaml")
                  if item["kind"] == "VMServiceScrape" and item["metadata"]["name"] == "re8ch-node-storage")
    endpoint = scrape["spec"]["endpoints"][0]
    assert endpoint["interval"] == "2m"
    assert endpoint["metricRelabelConfigs"][0]["action"] == "keep"
    assert "node_memory_Swap" in endpoint["metricRelabelConfigs"][0]["regex"]
    service = next(item for item in docs("platform/observability/production/node-storage-exporter.yaml")
                   if item["kind"] == "Service")
    job = service["metadata"]["name"]
    rules = docs("platform/observability/production/rules.yaml")[0]["spec"]["groups"]
    storage = next(group for group in rules if group["name"] == "re8ch.storage.alerts")
    by_name = {rule["alert"]: rule for rule in storage["rules"]}
    for name in ("Re8chReadyNodeFilesystemLow", "Re8chReadyNodeFilesystemCritical", "Re8chReadyNodeSwapHigh"):
        assert 'kube_node_status_condition{condition="Ready",status="true"}' in by_name[name]["expr"]
        assert 'job="%s"' % job in by_name[name]["expr"]
    assert "unless on (node)" in by_name["Re8chReadyNodeStorageTelemetryMissing"]["expr"]
    assert 'job="%s"' % job in by_name["Re8chReadyNodeStorageTelemetryMissing"]["expr"]


def test_storage_tools_are_published_on_read_only_cluster_plane():
    publication = next(item for item in docs("clusters/re8ch/components/toolhive/external-observe-publication.yaml")
                       if item["kind"] == "MCPToolConfig" and item["metadata"]["name"] == "ops-public-read-tools")
    tools = set(publication["spec"]["toolsFilter"])
    assert {"get_ceph_storage_status", "get_ready_node_storage_inventory"} <= tools
