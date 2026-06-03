#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.192}"
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

cluster_ip = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "svc", "panel-victoria-metrics", "-o", "jsonpath={.spec.clusterIP}"],
    text=True,
).strip()
vm_url = f"http://{cluster_ip}:8428"

def vm_query(query):
    url = vm_url + "/api/v1/query?query=" + urllib.parse.quote(query, safe="")
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)

health = vm_query("count(pg_up)")
health_result = health.get("data", {}).get("result", [])
if not health_result:
    raise SystemExit("VictoriaMetrics returned no pg_up data")
print(f"vm_url={vm_url}")
print(f"pg_up_count={health_result[0]['value'][1]}")

re8ch_checks = {
    "coredns_series": 'count(coredns_build_info{job="k8s-coredns"})',
    "http_probe_series": 'count(probe_success{job="http-probe"})',
    "nuclio_info_series": "count(qianwen_nuclio_function_info)",
    "nuclio_scrape_targets": 'count(up{job=~"k8s-nuclio-functions|k8s-nuclio-edge"})',
}
for name, query in re8ch_checks.items():
    result = vm_query(query).get("data", {}).get("result", [])
    value = result[0]["value"][1] if result else "0"
    print(f"{name}={value}")

tenant_result = vm_query("count(qianwen_nuclio_function_info) by (tenant)").get("data", {}).get("result", [])
if tenant_result:
    tenants = ", ".join(
        f"{item.get('metric', {}).get('tenant', '<none>')}={item['value'][1]}"
        for item in tenant_result
    )
    print(f"nuclio_tenants={tenants}")

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
    result = vm_query('count({__name__="%s"})' % metric).get("data", {}).get("result", [])
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
