#!/usr/bin/env bash
set -euo pipefail

PIGSTY_HOST="${PIGSTY_HOST:-ubuntu@192.0.2.192}"
NAMESPACE="${NAMESPACE:-qianwen-ops}"
SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=6)

ssh "${SSH_OPTS[@]}" "$PIGSTY_HOST" "NAMESPACE='$NAMESPACE' python3 -u -" <<'PY'
import base64
import json
import os
import subprocess
import urllib.parse
import urllib.request

namespace = os.environ["NAMESPACE"]

def service_url(name, port):
    ip = subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "svc", name, "-o", "jsonpath={.spec.clusterIP}"],
        text=True,
    ).strip()
    return "http://%s:%s" % (ip, port)

k3s_vm = service_url("panel-victoria-metrics", 8428)
vlogs = service_url("panel-victoria-logs", 9428)
tenant_metrics = service_url("panel-tenant-metrics-proxy", 39091) + "/qianwen"
tenant_logs = service_url("panel-tenant-logs-proxy", 39092) + "/qianwen"
pigsty_vm = "http://192.0.2.192:8428"
grafana = service_url("panel-grafana", 3000) + "/grafana"

def get_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)

def vm_query(base_url, query):
    data = get_json(base_url + "/api/v1/query?query=" + urllib.parse.quote(query, safe=""))
    result = data.get("data", {}).get("result", [])
    return result

def vlogs_query(base_url, query, limit=1):
    path = "/select/logsql/query"
    if base_url.endswith("/qianwen"):
        path = "/select/logsql/query"
    url = base_url + path + "?" + urllib.parse.urlencode({"query": query, "limit": str(limit)})
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = response.read().decode()
    return [line for line in payload.splitlines() if line.strip()]

def decode_secret_key(secret, key):
    value = subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "secret", secret, "-o", "jsonpath={.data.%s}" % key],
        text=True,
    ).strip()
    return base64.b64decode(value).decode()

grafana_auth = "Basic " + base64.b64encode(
    ("%s:%s" % (
        decode_secret_key("panel-grafana-admin-recovery", "username"),
        decode_secret_key("panel-grafana-admin-recovery", "password"),
    )).encode()
).decode()

def grafana_api(path, org_id=None):
    headers = {"Authorization": grafana_auth, "Accept": "application/json"}
    if org_id is not None:
        headers["X-Grafana-Org-Id"] = str(org_id)
    req = urllib.request.Request(grafana + path, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

def assert_nonempty(name, result):
    if not result:
        raise SystemExit("%s returned no data" % name)
    print("%s=ok count=%s" % (name, len(result)))

def assert_dashboard(uid, org_id=None):
    grafana_api("/api/dashboards/uid/%s" % uid, org_id=org_id)
    print("dashboard:%s:org%s=ok" % (uid, org_id or 1))

def assert_labels(source_uid, org_id, labels):
    rows = grafana_api("/api/datasources/uid/%s/correlations" % source_uid, org_id=org_id)
    found = {row.get("label") for row in rows}
    missing = [label for label in labels if label not in found]
    if missing:
        raise SystemExit("correlations missing for %s org=%s: %s" % (source_uid, org_id or 1, ", ".join(missing)))
    print("correlations:%s:org%s=ok labels=%s" % (source_uid, org_id or 1, len(labels)))

assert_labels("ds-prometheus", None, ["Show Nuclio logs for function", "Show pod logs"])
assert_labels("re8ch-vlogs", None, ["Function metadata metrics", "Pod scrape metrics", "Node CPU metrics", "Nuclio functions dashboard"])
assert_labels("pigsty-metrics", None, [
    "Open PGRDS cluster dashboard",
    "Open PGSQL instance dashboard",
    "Open PGRDS instance dashboard",
    "Open PGSQL database dashboard",
    "Open PGCAT database dashboard",
    "Open PGSQL table dashboard",
    "Open PGCAT table dashboard",
])
assert_labels("tenant-vlogs", 2, ["Function metadata metrics", "Pod scrape metrics", "Node CPU metrics"])

up_samples = vm_query(k3s_vm, 'up{tenant="qianwen",pod!="",function!=""}')
assert_nonempty("sample:qwen_function_up", up_samples)
sample = up_samples[0]["metric"]
function = sample["function"]
pod = sample["pod"]
node = sample["node"]

assert_nonempty(
    "main:prom_function_to_vlogs",
    vlogs_query(vlogs, "job:nuclio-pod-logs kubernetes.pod_labels.nuclio.io/function-name:%s" % function),
)
assert_nonempty(
    "main:prom_pod_to_vlogs",
    vlogs_query(vlogs, "job:nuclio-pod-logs kubernetes.pod_name:%s" % pod),
)
assert_nonempty(
    "main:vlogs_function_to_metrics",
    vm_query(k3s_vm, 'qianwen_nuclio_function_info{function="%s"}' % function),
)
assert_nonempty(
    "main:vlogs_pod_to_metrics",
    vm_query(k3s_vm, 'up{pod="%s"}' % pod),
)
assert_nonempty(
    "main:vlogs_node_to_metrics",
    vm_query(k3s_vm, 'rate(node_cpu_seconds_total{node="%s"}[5m])' % node),
)
assert_dashboard("re8ch-nuclio-functions")

assert_nonempty(
    "tenant:vlogs_function_to_metrics",
    vm_query(tenant_metrics, 'qianwen_nuclio_function_info{tenant="qianwen",function="%s"}' % function),
)
assert_nonempty(
    "tenant:vlogs_pod_to_metrics",
    vm_query(tenant_metrics, 'up{tenant="qianwen",pod="%s"}' % pod),
)
assert_nonempty(
    "tenant:vlogs_node_to_metrics",
    vm_query(tenant_metrics, 'rate(node_cpu_seconds_total{node="%s"}[5m])' % node),
)
assert_nonempty(
    "tenant:logs_scope",
    vlogs_query(tenant_logs, "job:nuclio-pod-logs kubernetes.pod_name:%s" % pod),
)

pg_up = vm_query(pigsty_vm, 'pg_up{cls="pg-meta",ins!=""}')
assert_nonempty("sample:pigsty_pg_up", pg_up)
pg = pg_up[0]["metric"]
cls = pg["cls"]
ins = pg["ins"]
assert_nonempty("pigsty:cls_to_cluster_object", vm_query(pigsty_vm, 'pg_up{cls="%s"}' % cls))
assert_nonempty("pigsty:ins_to_instance_object", vm_query(pigsty_vm, 'pg_up{ins="%s"}' % ins))
assert_dashboard("pgrds-cluster")
assert_dashboard("pgsql-instance")
assert_dashboard("pgrds-instance")

db_rows = vm_query(pigsty_vm, 'pg_table_size_bytes{cls="pg-meta",datname="qianwen_prod",relname!=""}')
assert_nonempty("sample:pigsty_table_metric", db_rows)
db_metric = db_rows[0]["metric"]
datname = db_metric["datname"]
relname = db_metric["relname"]
assert_nonempty("pigsty:datname_to_database_object", vm_query(pigsty_vm, 'pg_db_active_time{datname="%s"}' % datname))
assert_nonempty("pigsty:relname_to_table_object", vm_query(pigsty_vm, 'pg_table_size_bytes{datname="%s",relname="%s"}' % (datname, relname)))
assert_dashboard("pgsql-database")
assert_dashboard("pgcat-database")
assert_dashboard("pgsql-table")
assert_dashboard("pgcat-table")

print("validated_function=%s" % function)
print("validated_pod=%s" % pod)
print("validated_node=%s" % node)
print("validated_pg_cls=%s" % cls)
print("validated_pg_ins=%s" % ins)
print("validated_pg_datname=%s" % datname)
print("validated_pg_relname=%s" % relname)
PY
