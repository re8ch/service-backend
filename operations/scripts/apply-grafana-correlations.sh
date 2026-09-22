#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-qianwen-ops}"
GRAFANA_URL="${GRAFANA_URL:-https://grafana.service.re8ch.com/grafana}"
ADMIN_SECRET="${ADMIN_SECRET:-panel-grafana-admin-recovery}"

python3 -u - <<'PY'
import base64
import json
import os
import subprocess
import urllib.error
import urllib.request

namespace = os.environ.get("NAMESPACE", "qianwen-ops")
grafana_url = os.environ.get("GRAFANA_URL", "https://grafana.service.re8ch.com/grafana").rstrip("/")
admin_secret = os.environ.get("ADMIN_SECRET", "panel-grafana-admin-recovery")

def kubectl_jsonpath(path):
    return subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "secret", admin_secret, "-o", "jsonpath=" + path],
        text=True,
    ).strip()

username = base64.b64decode(kubectl_jsonpath("{.data.username}")).decode()
password = base64.b64decode(kubectl_jsonpath("{.data.password}")).decode()
auth = "Basic " + base64.b64encode(("%s:%s" % (username, password)).encode()).decode()

def request(method, path, body=None, org_id=None):
    headers = {"Authorization": auth, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if org_id is not None:
        headers["X-Grafana-Org-Id"] = str(org_id)
    req = urllib.request.Request(grafana_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = response.read()
            return response.status, json.loads(payload.decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            payload = json.loads(payload)
        except Exception:
            pass
        raise RuntimeError("%s %s failed: HTTP %s %s" % (method, path, exc.code, payload))

def ensure_datasource(uid, org_id):
    status, _ = request("GET", "/api/datasources/uid/%s" % uid, org_id=org_id)
    if status != 200:
        raise RuntimeError("datasource %s in org %s is not readable" % (uid, org_id or 1))

def ensure_correlation(source_uid, org_id, correlation):
    ensure_datasource(source_uid, org_id)
    ensure_datasource(correlation["targetUID"], org_id)
    _, existing = request("GET", "/api/datasources/uid/%s/correlations" % source_uid, org_id=org_id)
    matched = None
    for item in existing:
        if item.get("label") == correlation["label"] and item.get("targetUID") == correlation["targetUID"]:
            matched = item
            desired = {
                "description": correlation.get("description", ""),
                "type": correlation.get("type", ""),
                "config": correlation.get("config", {}),
            }
            current = {
                "description": item.get("description", ""),
                "type": item.get("type", ""),
                "config": item.get("config", {}),
            }
            if json.dumps(current, sort_keys=True) == json.dumps(desired, sort_keys=True):
                print("correlation=present org=%s source=%s target=%s label=%s" % (org_id or 1, source_uid, correlation["targetUID"], correlation["label"]))
                return
            correlation_uid = item.get("uid")
            request("DELETE", "/api/datasources/uid/%s/correlations/%s" % (source_uid, correlation_uid), org_id=org_id)
            print("correlation=replacing org=%s source=%s target=%s label=%s old_uid=%s" % (org_id or 1, source_uid, correlation["targetUID"], correlation["label"], correlation_uid))
            break
    _, created = request("POST", "/api/datasources/uid/%s/correlations" % source_uid, correlation, org_id=org_id)
    created_id = (created.get("result") or {}).get("uid", "")
    print("correlation=created org=%s source=%s target=%s label=%s uid=%s" % (org_id or 1, source_uid, correlation["targetUID"], correlation["label"], created_id))

def delete_correlation_label(source_uid, org_id, label, target_uid=None):
    ensure_datasource(source_uid, org_id)
    _, existing = request("GET", "/api/datasources/uid/%s/correlations" % source_uid, org_id=org_id)
    for item in existing:
        if item.get("label") == label and (target_uid is None or item.get("targetUID") == target_uid):
            request("DELETE", "/api/datasources/uid/%s/correlations/%s" % (source_uid, item.get("uid")), org_id=org_id)
            print("correlation=deleted org=%s source=%s target=%s label=%s uid=%s" % (org_id or 1, source_uid, item.get("targetUID"), label, item.get("uid")))

main = [
    ("ds-prometheus", None, {
        "targetUID": "re8ch-vlogs",
        "label": "Show Nuclio logs for function",
        "description": "Open VictoriaLogs for the selected Nuclio function label.",
        "type": "query",
        "config": {"field": "function", "target": {"query": "job:nuclio-pod-logs kubernetes.pod_labels.nuclio.io/function-name:${__value.raw}"}},
    }),
    ("ds-prometheus", None, {
        "targetUID": "re8ch-vlogs",
        "label": "Show pod logs",
        "description": "Open VictoriaLogs for the selected Kubernetes pod label.",
        "type": "query",
        "config": {"field": "pod", "target": {"query": "job:nuclio-pod-logs kubernetes.pod_name:${__value.raw}"}},
    }),
    ("re8ch-vlogs", None, {
        "targetUID": "ds-prometheus",
        "label": "Function metadata metrics",
        "description": "Query the stable Nuclio function metadata series for the selected log function.",
        "type": "query",
        "config": {"field": "kubernetes.pod_labels.nuclio.io/function-name", "target": {"expr": 'qianwen_nuclio_function_info{function="${__value.raw}"}'}},
    }),
    ("re8ch-vlogs", None, {
        "targetUID": "ds-prometheus",
        "label": "Pod scrape metrics",
        "description": "Query scrape state for the selected log pod.",
        "type": "query",
        "config": {"field": "kubernetes.pod_name", "target": {"expr": 'up{pod="${__value.raw}"}'}},
    }),
    ("re8ch-vlogs", None, {
        "targetUID": "ds-prometheus",
        "label": "Node CPU metrics",
        "description": "Query node CPU metrics for the selected log node.",
        "type": "query",
        "config": {"field": "kubernetes.pod_node_name", "target": {"expr": 'rate(node_cpu_seconds_total{node="${__value.raw}"}[5m])'}},
    }),
    ("re8ch-vlogs", None, {
        "targetUID": "ds-prometheus",
        "label": "Nuclio functions dashboard",
        "description": "Open the admin Nuclio functions dashboard scoped by function.",
        "type": "external",
        "config": {"field": "kubernetes.pod_labels.nuclio.io/function-name", "target": {"url": "/grafana/d/re8ch-nuclio-functions/re8ch-nuclio-functions?orgId=1&var-function=${__value.raw}"}},
    }),
]

pigsty = [
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGRDS cluster dashboard",
        "description": "Open the Pigsty PGRDS cluster dashboard for the selected cls label.",
        "type": "external",
        "config": {"field": "cls", "target": {"url": "/grafana/d/pgrds-cluster/pgrds-cluster?orgId=1&var-cls=${__value.raw}"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGSQL instance dashboard",
        "description": "Open the Pigsty PGSQL instance dashboard for the selected ins label.",
        "type": "external",
        "config": {"field": "ins", "target": {"url": "/grafana/d/pgsql-instance/pgsql-instance?orgId=1&var-cls=pg-meta&var-ins=${__value.raw}"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGRDS instance dashboard",
        "description": "Open the Pigsty PGRDS instance dashboard for the selected ins label.",
        "type": "external",
        "config": {"field": "ins", "target": {"url": "/grafana/d/pgrds-instance/pgrds-instance?orgId=1&var-cls=pg-meta&var-ins=${__value.raw}"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGSQL database dashboard",
        "description": "Open the Pigsty PGSQL database dashboard for the selected datname label.",
        "type": "external",
        "config": {"field": "datname", "target": {"url": "/grafana/d/pgsql-database/pgsql-database?orgId=1&var-cls=pg-meta&var-ins=pg-meta-3&var-datname=${__value.raw}"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGCAT database dashboard",
        "description": "Open the PGCAT database dashboard for the selected datname label.",
        "type": "external",
        "config": {"field": "datname", "target": {"url": "/grafana/d/pgcat-database/pgcat-database?orgId=1&var-dsn=pg-meta-3.${__value.raw}&var-datname=${__value.raw}&var-ins=pg-meta-3&var-cls=pg-meta"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGSQL table dashboard",
        "description": "Open the Pigsty PGSQL table dashboard for the selected relname label.",
        "type": "external",
        "config": {"field": "relname", "target": {"url": "/grafana/d/pgsql-table/pgsql-table?orgId=1&var-cls=pg-meta&var-datname=qianwen_prod&var-relname=${__value.raw}"}},
    }),
    ("pigsty-metrics", None, {
        "targetUID": "pigsty-metrics",
        "label": "Open PGCAT table dashboard",
        "description": "Open the PGCAT table dashboard for the selected relname label.",
        "type": "external",
        "config": {"field": "relname", "target": {"url": "/grafana/d/pgcat-table/pgcat-table?orgId=1&var-dsn=pg-meta-3.qianwen_prod&var-datname=qianwen_prod&var-relname=${__value.raw}"}},
    }),
]

tenant_qianwen = [
    ("tenant-vlogs", 2, {
        "targetUID": "tenant-metrics",
        "label": "Function metadata metrics",
        "description": "Query tenant-scoped stable Nuclio function metadata for the selected log function.",
        "type": "query",
        "config": {"field": "kubernetes.pod_labels.nuclio.io/function-name", "target": {"expr": 'qianwen_nuclio_function_info{tenant="qianwen",function="${__value.raw}"}'}},
    }),
    ("tenant-vlogs", 2, {
        "targetUID": "tenant-metrics",
        "label": "Pod scrape metrics",
        "description": "Query tenant-scoped scrape state for the selected pod.",
        "type": "query",
        "config": {"field": "kubernetes.pod_name", "target": {"expr": 'up{tenant="qianwen",pod="${__value.raw}"}'}},
    }),
    ("tenant-vlogs", 2, {
        "targetUID": "tenant-metrics",
        "label": "Node CPU metrics",
        "description": "Query node CPU metrics for the selected node.",
        "type": "query",
        "config": {"field": "kubernetes.pod_node_name", "target": {"expr": 'rate(node_cpu_seconds_total{node="${__value.raw}"}[5m])'}},
    }),
]

delete_correlation_label("re8ch-vlogs", None, "Function traffic metrics", "ds-prometheus")
delete_correlation_label("tenant-vlogs", 2, "Function traffic metrics", "tenant-metrics")
delete_correlation_label("tenant-vlogs", 2, "Tenant dashboard", "tenant-metrics")

for source_uid, org_id, correlation in main + pigsty + tenant_qianwen:
    ensure_correlation(source_uid, org_id, correlation)
PY
