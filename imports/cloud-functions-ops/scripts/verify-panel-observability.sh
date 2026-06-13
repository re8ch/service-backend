#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.67}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "NAMESPACE='$NAMESPACE' python3 -u -" <<'PY'
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

namespace = os.environ["NAMESPACE"]
dashboard_dir = "/home/ubuntu/pigsty/files/grafana/pgsql"
metric_re = re.compile(r"(?<![A-Za-z0-9_:])([A-Za-z_:][A-Za-z0-9_:]*)(?=\s*(?:\{|\[))")
pigsty_vm_url = "http://192.0.2.67:8428"

cluster_ip = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "svc", "panel-victoria-metrics", "-o", "jsonpath={.spec.clusterIP}"],
    text=True,
).strip()
vm_url = f"http://{cluster_ip}:8428"

def vm_query(base_url, query):
    url = base_url + "/api/v1/query?query=" + urllib.parse.quote(query, safe="")
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)

health = vm_query(vm_url, "count(pg_up)")
health_result = health.get("data", {}).get("result", [])
if not health_result:
    raise SystemExit("VictoriaMetrics returned no pg_up data")
print(f"vm_url={vm_url}")
print(f"pg_up_count={health_result[0]['value'][1]}")

pigsty_health = vm_query(pigsty_vm_url, "count(up)")
pigsty_health_result = pigsty_health.get("data", {}).get("result", [])
if not pigsty_health_result:
    raise SystemExit("Pigsty VictoriaMetrics returned no up data")
print(f"pigsty_vm_url={pigsty_vm_url}")
print(f"pigsty_up_count={pigsty_health_result[0]['value'][1]}")

datasources = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "cm", "panel-grafana-datasources", "-o", "jsonpath={.data.datasources\\.yaml}"],
    text=True,
)
if "uid: pigsty-metrics" not in datasources or "url: http://192.0.2.67:8428" not in datasources:
    raise SystemExit("panel Grafana is missing the Pigsty Metrics datasource")
print("pigsty_metrics_datasource=present")

with open("/infra/prometheus.yml") as f:
    pigsty_prometheus = f.read().lower()
for forbidden in ("kubernetes_sd_configs", "nuclio", "kubelet", "cadvisor"):
    if forbidden in pigsty_prometheus:
        raise SystemExit(f"ICP Pigsty VM config unexpectedly contains {forbidden}")
print("pigsty_vm_scope=infra_pg_only")

cluster_obs_yaml = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "deploy,ds,cm", "-o", "yaml"],
    text=True,
)
if "192.0.2.67:30428" in cluster_obs_yaml:
    raise SystemExit("K3S observability still references ICP NodePort 192.0.2.67:30428")
print("remote_write_path=headlamp_service_dns")

old_edge = subprocess.run(
    ["kubectl", "-n", namespace, "get", "deploy", "panel-qwen-nodeport-vmagent"],
    text=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if old_edge.returncode == 0:
    raise SystemExit("old panel-qwen-nodeport-vmagent deployment still exists")
print("old_edge_vmagent=absent")

placements = {
    "panel-grafana": "rancher-panel",
    "panel-victoria-metrics": "rancher-panel",
    "panel-re8ch-vmalert": "rancher-panel",
}
for app, expected_node in placements.items():
    node = subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "pod", "-l", f"app.kubernetes.io/name={app}", "-o", "jsonpath={.items[0].spec.nodeName}"],
        text=True,
    ).strip()
    if node != expected_node:
        raise SystemExit(f"{app} is on {node}, expected {expected_node}")
    print(f"{app}_node={node}")

re8ch_checks = {
    "coredns_series": 'count(coredns_build_info{job="k8s-coredns"})',
    "http_probe_series": 'count(probe_success{job="http-probe"})',
    "nuclio_info_series": "count(qianwen_nuclio_function_info)",
    "nuclio_processor_series": 'count({__name__=~"nuclio_processor_.*"})',
    "traefik_series": 'count({__name__=~"traefik_.*"})',
    "harbor_container_cpu_series": 'count(container_cpu_usage_seconds_total{namespace="harbor"})',
    "harbor_probe_series": 'count(probe_success{job="http-probe",category="harbor"})',
    "trivy_series": 'count({job="harbor-trivy-adapter",__name__=~"trivy_.*|go_goroutines|process_.*"})',
    "edge_series": 'count({__name__=~"qianwen_edge_.*|api_edge_.*"})',
}
for name, query in re8ch_checks.items():
    result = vm_query(vm_url, query).get("data", {}).get("result", [])
    value = result[0]["value"][1] if result else "0"
    print(f"{name}={value}")

edge_result = vm_query(vm_url, 'count({__name__=~"qianwen_edge_.*|api_edge_.*"})').get("data", {}).get("result", [])
edge_value = float(edge_result[0]["value"][1]) if edge_result else 0.0
if edge_value:
    raise SystemExit(f"deprecated edge metrics are still present: {edge_value:g}")

dashboard_grep = subprocess.run(
    [
        "kubectl",
        "-n",
        namespace,
        "exec",
        "deploy/panel-grafana",
        "--",
        "sh",
        "-lc",
        "grep -RIn 'Nuclio Edge Router\\|qianwen_edge_\\|api_edge_' /var/lib/grafana/dashboards/qianwen 2>/dev/null",
    ],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if dashboard_grep.returncode == 0:
    raise SystemExit("deprecated edge dashboard references remain:\n" + dashboard_grep.stdout)
print("edge_dashboards=absent")

tenant_result = vm_query(vm_url, "count(qianwen_nuclio_function_info) by (tenant)").get("data", {}).get("result", [])
if tenant_result:
    tenants = ", ".join(
        f"{item.get('metric', {}).get('tenant', '<none>')}={item['value'][1]}"
        for item in tenant_result
    )
    print(f"nuclio_tenants={tenants}")
    if not any(item.get("metric", {}).get("tenant") == "qianwen" for item in tenant_result):
        raise SystemExit("qianwen_nuclio_function_info is missing tenant=qianwen")
else:
    raise SystemExit("qianwen_nuclio_function_info returned no tenant series")

dashboards = {}
for name in sorted(os.listdir(dashboard_dir)):
    if not name.endswith(".json") or not name.startswith(("pgsql-", "pgrds-")):
        continue
    with open(os.path.join(dashboard_dir, name)) as f:
        dashboard = json.load(f)
    metrics = set()

    def add_expr(expr):
        if not isinstance(expr, str):
            return
        for metric in metric_re.findall(expr):
            if not metric.startswith("__"):
                metrics.add(metric)

    for variable in dashboard.get("templating", {}).get("list", []):
        query = variable.get("query")
        if isinstance(query, dict):
            query = query.get("query")
        add_expr(query or "")

    def walk(panels):
        for panel in panels or []:
            if "panels" in panel:
                walk(panel.get("panels"))
            for target in panel.get("targets", []) or []:
                if "expr" in target:
                    add_expr(target.get("expr"))

    walk(dashboard.get("panels"))
    dashboards[name] = sorted(metrics)

all_metrics = sorted({metric for values in dashboards.values() for metric in values})
counts = {}
for index, metric in enumerate(all_metrics, 1):
    result = vm_query(vm_url, 'count({__name__="%s"})' % metric).get("data", {}).get("result", [])
    counts[metric] = int(float(result[0]["value"][1])) if result else 0
    if index % 50 == 0:
        print(f"queried={index}/{len(all_metrics)}", flush=True)

present = sum(1 for value in counts.values() if value)
missing = len(counts) - present
print(f"metrics={len(counts)} present={present} missing_or_zero={missing}")

for dashboard, metric_names in dashboards.items():
    missing_names = [name for name in metric_names if not counts.get(name)]
    if missing_names:
        print(f"{dashboard} missing={len(missing_names)} {', '.join(missing_names)}")
PY
