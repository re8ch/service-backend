import base64
import json
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TENANT = os.getenv("TENANT_NAMESPACE", "qwen")
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8088"))
OPS_TOKEN = os.getenv("OPS_TOKEN", "")
OPS_ADMIN_TOKEN = os.getenv("OPS_ADMIN_TOKEN", "")
KUBE_SERVER_PUBLIC = os.getenv("KUBE_SERVER_PUBLIC", "https://192.0.2.67:6443")
REGISTRY_PUBLIC = os.getenv("REGISTRY_PUBLIC", "https://registry.re8ch.com")
REGISTRY_ALT = os.getenv("REGISTRY_ALT", "https://registry.ynzs.com")
GRAFANA_PUBLIC = os.getenv("GRAFANA_PUBLIC", "https://grafana.re8ch.com")
HEADLAMP_PUBLIC = os.getenv("HEADLAMP_PUBLIC", "https://headlamp.re8ch.com")
HARBOR_URL = os.getenv("HARBOR_URL", REGISTRY_PUBLIC).rstrip("/")
HARBOR_API_URL = os.getenv("HARBOR_API_URL", HARBOR_URL).rstrip("/")
HARBOR_SKIP_TLS_VERIFY = os.getenv("HARBOR_SKIP_TLS_VERIFY", "").lower() in ("1", "true", "yes")
HARBOR_USERNAME = os.getenv("HARBOR_USERNAME", "")
HARBOR_PASSWORD = os.getenv("HARBOR_PASSWORD", "")
TENANT_HARBOR_PROJECT = os.getenv("TENANT_HARBOR_PROJECT", TENANT)
TENANT_HARBOR_SECRET = os.getenv("TENANT_HARBOR_SECRET", "harbor-%s-admin" % TENANT_HARBOR_PROJECT)
TENANT_HARBOR_ROBOT = os.getenv("TENANT_HARBOR_ROBOT", "robot$%s+%s-admin-20260606" % (TENANT_HARBOR_PROJECT, TENANT_HARBOR_PROJECT))
METRICS_URL = os.getenv("METRICS_URL", "http://127.0.0.1:30428").rstrip("/")
TENANT_METRICS_TENANT = os.getenv("TENANT_METRICS_TENANT", TENANT_HARBOR_PROJECT)
TENANT_NAMESPACES = [x.strip() for x in os.getenv("TENANT_NAMESPACES", TENANT).split(",") if x.strip()]
TENANT_DB_NAMES = [x.strip() for x in os.getenv("TENANT_DB_NAMES", "").split(",") if x.strip()]
TENANT_GRAFANA_DASHBOARD = os.getenv("TENANT_GRAFANA_DASHBOARD", "")
TENANT_GRAFANA_TOKEN_SECRET = os.getenv("TENANT_GRAFANA_TOKEN_SECRET", "grafana-%s-viewer" % TENANT_METRICS_TENANT)
SERVICE_ACCOUNT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_API = "https://%s:%s" % (
    os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
    os.getenv("KUBERNETES_SERVICE_PORT", "443"),
)

class KubeError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


RESOURCE_PATHS = {
    "pods": "/api/v1/namespaces/%s/pods",
    "services": "/api/v1/namespaces/%s/services",
    "configmaps": "/api/v1/namespaces/%s/configmaps",
    "secrets": "/api/v1/namespaces/%s/secrets",
    "events": "/api/v1/namespaces/%s/events",
    "pvcs": "/api/v1/namespaces/%s/persistentvolumeclaims",
    "deployments": "/apis/apps/v1/namespaces/%s/deployments",
    "replicasets": "/apis/apps/v1/namespaces/%s/replicasets",
    "statefulsets": "/apis/apps/v1/namespaces/%s/statefulsets",
    "daemonsets": "/apis/apps/v1/namespaces/%s/daemonsets",
    "jobs": "/apis/batch/v1/namespaces/%s/jobs",
    "cronjobs": "/apis/batch/v1/namespaces/%s/cronjobs",
    "ingresses": "/apis/networking.k8s.io/v1/namespaces/%s/ingresses",
    "nucliofunctions": "/apis/nuclio.io/v1beta1/namespaces/%s/nucliofunctions",
    "nuclioprojects": "/apis/nuclio.io/v1beta1/namespaces/%s/nuclioprojects",
    "nuclioapigateways": "/apis/nuclio.io/v1beta1/namespaces/%s/nuclioapigateways",
    "podmetrics": "/apis/metrics.k8s.io/v1beta1/namespaces/%s/pods",
}

RESOURCE_KINDS = {
    "pods": "Pod",
    "services": "Service",
    "configmaps": "ConfigMap",
    "secrets": "Secret",
    "events": "Event",
    "pvcs": "PersistentVolumeClaim",
    "deployments": "Deployment",
    "replicasets": "ReplicaSet",
    "statefulsets": "StatefulSet",
    "daemonsets": "DaemonSet",
    "jobs": "Job",
    "cronjobs": "CronJob",
    "ingresses": "Ingress",
    "nucliofunctions": "NuclioFunction",
    "nuclioprojects": "NuclioProject",
    "nuclioapigateways": "NuclioAPIGateway",
    "podmetrics": "PodMetrics",
    "nodes": "Node",
    "nodemetrics": "NodeMetrics",
}

GUIDE = """Ops MCP for re8ch/qwen

Public URLs:
- Headlamp: https://headlamp.re8ch.com
- Grafana:  https://grafana.re8ch.com
- Registry: https://registry.re8ch.com

Authentication:
- HTTP API and MCP both require Authorization: Bearer <ops-token>
- You can also send X-Ops-Token: <ops-token>

HTTP API:
- GET  /healthz
- GET  /api/me
- GET  /api/resources?kind=pods
- GET  /api/resources?kind=deployments
- GET  /api/resources?kind=services
- GET  /api/resources?kind=nodes
- GET  /api/resources?kind=podmetrics
- GET  /api/kubeconfig
- GET  /api/registry/info
- GET  /api/grafana/links
- GET  /api/grafana/access
- GET  /api/metrics/summary
- POST /api/restart {"deployment":"name"}
- GET  /api/admin/dex-users                    admin token required
- GET  /api/admin/dex-hash-instructions        admin token required
- POST /api/admin/ensure-tenant-access         admin token required
- POST /api/admin/create-dex-user              admin token required
- POST /api/admin/grant-user-namespace         admin token required
- POST /api/admin/ensure-harbor-tenant         admin token required

MCP endpoint:
- POST /mcp with JSON-RPC 2.0
- Supported methods: initialize, tools/list, tools/call

Grafana for AI agents:
- Prefer MCP tool get_tenant_metrics_summary for compact tenant health summaries.
- Use MCP tool get_grafana_access only when the agent needs Grafana dashboard JSON or raw Grafana datasource query API.
- get_grafana_access returns a Viewer-scoped Grafana service account token for this tenant org.
- Use the returned token with: Authorization: Bearer <grafana-service-account-token>
- Dashboard discovery: GET https://grafana.re8ch.com/ui/api/search?type=dash-db
- Dashboard JSON: GET https://grafana.re8ch.com/ui/api/dashboards/uid/<dashboard-uid>
- Datasource query: POST https://grafana.re8ch.com/ui/api/ds/query with datasource uid tenant-metrics.
- The tenant Grafana datasource still goes through the tenant metrics proxy, so arbitrary PromQL and cross-tenant queries are rejected.
- Do not use human Grafana web passwords for AI agents.

Safety boundary:
- Tenant namespace: qwen
- Normal token writes are limited to restarting deployments in qwen.
- Node access is read-only for topology and metrics.
- Admin token can reconcile Dex static users and tenant RBAC.
"""

def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()

def kube_request(method, path, body=None, content_type="application/json"):
    token = read_file(SERVICE_ACCOUNT_TOKEN)
    data = None
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        headers["Content-Type"] = content_type
    req = urllib.request.Request(KUBE_API + path, data=data, method=method, headers=headers)
    context = ssl.create_default_context(cafile=SERVICE_ACCOUNT_CA)
    try:
        with urllib.request.urlopen(req, timeout=12, context=context) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise KubeError(exc.code, "kubernetes %s %s failed: %s %s" % (method, path, exc.code, raw))

def resource_path(kind):
    if kind == "nodes":
        return "/api/v1/nodes"
    if kind == "nodemetrics":
        return "/apis/metrics.k8s.io/v1beta1/nodes"
    if kind not in RESOURCE_PATHS:
        raise ValueError("unsupported kind: %s" % kind)
    return RESOURCE_PATHS[kind] % TENANT

def simplify_resource(item, default_kind=None):
    meta = item.get("metadata", {})
    kind = item.get("kind") or default_kind
    status = item.get("status", {})
    spec = item.get("spec", {})
    out = {
        "kind": kind,
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "labels": meta.get("labels", {}),
        "creationTimestamp": meta.get("creationTimestamp"),
    }
    if kind == "Pod":
        out["phase"] = status.get("phase")
        out["nodeName"] = spec.get("nodeName")
        out["podIP"] = status.get("podIP")
        out["containers"] = [c.get("name") for c in spec.get("containers", [])]
    elif kind == "Node":
        out["addresses"] = status.get("addresses", [])
        out["conditions"] = [
            {"type": c.get("type"), "status": c.get("status")} for c in status.get("conditions", [])
        ]
    elif kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
        out["replicas"] = status.get("replicas")
        out["readyReplicas"] = status.get("readyReplicas")
        out["availableReplicas"] = status.get("availableReplicas")
    elif kind == "Service":
        out["type"] = spec.get("type")
        out["clusterIP"] = spec.get("clusterIP")
        out["ports"] = spec.get("ports", [])
    elif kind == "Event":
        out["reason"] = item.get("reason")
        out["message"] = item.get("message")
        out["type"] = item.get("type")
        out["lastTimestamp"] = item.get("lastTimestamp") or item.get("eventTime")
    else:
        out["status"] = status if status else None
    return out

def list_resources(kind):
    doc = kube_request("GET", resource_path(kind))
    items = doc.get("items", [])
    default_kind = RESOURCE_KINDS.get(kind)
    return {"kind": kind, "tenant": TENANT, "count": len(items), "items": [simplify_resource(x, default_kind) for x in items]}

def get_secret_token(secret_name):
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (TENANT, secret_name))
    encoded = doc.get("data", {}).get("token")
    if not encoded:
        raise RuntimeError("secret %s has no data.token" % secret_name)
    return base64.b64decode(encoded).decode("utf-8")

def get_secret_data(secret_name):
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (TENANT, secret_name))
    data = {}
    for key, value in doc.get("data", {}).items():
        data[key] = base64.b64decode(value).decode("utf-8")
    return data

def build_kubeconfig():
    token = get_secret_token("qwen-kubectl-operator-permanent-token")
    ca_data = base64.b64encode(open(SERVICE_ACCOUNT_CA, "rb").read()).decode("ascii")
    return """apiVersion: v1
kind: Config
clusters:
- name: re8ch-k3s
  cluster:
    server: %s
    certificate-authority-data: %s
contexts:
- name: qwen@re8ch-k3s
  context:
    cluster: re8ch-k3s
    namespace: %s
    user: qwen-operator
current-context: qwen@re8ch-k3s
users:
- name: qwen-operator
  user:
    token: %s
""" % (KUBE_SERVER_PUBLIC, ca_data, TENANT, token)

def restart_deployment(name):
    if not name or "/" in name:
        raise ValueError("deployment name is required")
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "ops.re8ch.com/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    }
                }
            }
        }
    }
    doc = kube_request(
        "PATCH",
        "/apis/apps/v1/namespaces/%s/deployments/%s" % (TENANT, urllib.parse.quote(name)),
        patch,
        "application/strategic-merge-patch+json",
    )
    return {"tenant": TENANT, "deployment": name, "generation": doc.get("metadata", {}).get("generation")}

def tenant_context():
    return {
        "tenant": TENANT,
        "kubeServer": KUBE_SERVER_PUBLIC,
        "headlamp": HEADLAMP_PUBLIC,
        "grafana": GRAFANA_PUBLIC,
        "registry": REGISTRY_PUBLIC,
        "registryAlternate": REGISTRY_ALT,
        "registryProject": TENANT_HARBOR_PROJECT,
        "allowedOperations": ["list qwen resources", "read nodes", "read metrics", "restart qwen deployments", "download qwen kubeconfig"],
    }

def registry_info():
    return {
        "primary": REGISTRY_PUBLIC,
        "alternate": REGISTRY_ALT,
        "tenantNamespace": TENANT,
        "projectPath": "/%s" % TENANT_HARBOR_PROJECT,
        "k8sPullSecret": TENANT_HARBOR_SECRET,
        "pullRobotUsername": TENANT_HARBOR_ROBOT,
        "examples": [
            "docker login registry.re8ch.com",
            "docker pull registry.re8ch.com/%s/<image>:<tag>" % TENANT_HARBOR_PROJECT,
            "kubectl -n %s get secret %s" % (TENANT, TENANT_HARBOR_SECRET),
        ],
    }

def grafana_links():
    return {
        "grafana": GRAFANA_PUBLIC,
        "purpose": "metrics and observability dashboards",
        "tenantDashboard": TENANT_GRAFANA_DASHBOARD,
        "suggestedUse": ["inspect service health", "check node and pod trends", "compare deployment impact"],
    }

def grafana_access():
    data = get_secret_data(TENANT_GRAFANA_TOKEN_SECRET)
    token = data.get("token")
    if not token:
        raise RuntimeError("secret %s has no data.token" % TENANT_GRAFANA_TOKEN_SECRET)
    url = data.get("url", GRAFANA_PUBLIC).rstrip("/")
    org_id = data.get("orgId", "")
    dashboard = data.get("dashboard", TENANT_GRAFANA_DASHBOARD)
    datasource_uid = data.get("datasourceUid", "tenant-metrics")
    dashboard_uid = dashboard.rstrip("/").split("/")[-2] if "/d/" in dashboard else "<dashboard-uid>"
    sample_expr = 'sum(rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m])) or vector(0)' % TENANT_METRICS_TENANT
    sample_ds_query = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "expr": sample_expr,
                "instant": True,
                "range": False,
            }
        ],
        "from": "now-5m",
        "to": "now",
    }
    return {
        "grafana": url,
        "tenant": TENANT_METRICS_TENANT,
        "namespace": TENANT,
        "orgId": org_id,
        "orgName": data.get("orgName", ""),
        "role": data.get("role", "Viewer"),
        "serviceAccount": data.get("serviceAccount", ""),
        "dashboard": dashboard,
        "datasourceUid": datasource_uid,
        "tokenType": "Grafana service account token",
        "token": token,
        "headers": {"Authorization": "Bearer <token>"},
        "examples": {
            "dashboard": dashboard,
            "searchDashboards": '%s/ui/api/search?type=dash-db' % url,
            "getDashboard": '%s/ui/api/dashboards/uid/%s' % (url, dashboard_uid),
            "queryDatasource": '%s/ui/api/ds/query' % url,
        },
        "grafanaApiHowTo": {
            "step1": "Call get_grafana_access through Ops MCP using the tenant Ops token.",
            "step2": "Read token, grafana, dashboard, datasourceUid, and examples from this response.",
            "step3": "Send Grafana API requests with Authorization: Bearer <token>.",
            "step4": "Use /ui/api/search?type=dash-db to list tenant-visible dashboards.",
            "step5": "Use /ui/api/dashboards/uid/<uid> to retrieve dashboard JSON and panel PromQL.",
            "step6": "Use /ui/api/ds/query to execute allowed tenant datasource queries.",
        },
        "curlExamples": {
            "searchDashboards": 'curl -sS -H "Authorization: Bearer <token>" "%s/ui/api/search?type=dash-db"' % url,
            "getDashboard": 'curl -sS -H "Authorization: Bearer <token>" "%s/ui/api/dashboards/uid/%s"' % (url, dashboard_uid),
            "queryDatasource": 'curl -sS -H "Authorization: Bearer <token>" -H "Content-Type: application/json" "%s/ui/api/ds/query" -d %s' % (url, json.dumps(sample_ds_query, ensure_ascii=False)),
        },
        "sampleDatasourceQuery": sample_ds_query,
        "safety": [
            "Use this token for tenant AI agents, not human browser login.",
            "The token is Viewer-scoped to the tenant Grafana org.",
            "The token only sees the tenant org dashboard and the tenant-metrics datasource.",
            "The tenant-metrics datasource is protected by the tenant metrics proxy allowlist.",
            "Cross-tenant database, namespace, and global label discovery queries are rejected.",
            "Do not store this token in source code or prompt history.",
            "For simple metrics summaries, prefer get_tenant_metrics_summary because it does not expose raw Grafana API access.",
        ],
    }


def prom_query(expr):
    url = METRICS_URL + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=15) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    if doc.get("status") != "success":
        raise RuntimeError("metrics query failed: %s" % doc)
    return doc.get("data", {}).get("result", [])


def prom_scalar(expr):
    result = prom_query(expr)
    if not result:
        return 0.0
    try:
        return float(result[0].get("value", [None, "0"])[1])
    except Exception:
        return 0.0


def prom_vector(expr, label_keys):
    out = []
    for item in prom_query(expr):
        metric = item.get("metric", {})
        try:
            value = float(item.get("value", [None, "0"])[1])
        except Exception:
            value = 0.0
        row = {key: metric.get(key, "") for key in label_keys}
        row["value"] = value
        out.append(row)
    return out


def regex_or_empty(values):
    return "|".join(re.escape(x) for x in values) if values else "$^"


def get_tenant_metrics_summary():
    tenant = TENANT_METRICS_TENANT
    ns_re = regex_or_empty(TENANT_NAMESPACES)
    db_re = regex_or_empty(TENANT_DB_NAMES)
    function_count = prom_scalar('count(qianwen_nuclio_function_info{tenant="%s"})' % tenant)
    request_rps = prom_scalar('sum(rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m]))' % tenant)
    error_rps = prom_scalar('sum(rate(qianwen_nuclio_function_errors_total{tenant="%s"}[5m]))' % tenant)
    p95 = prom_scalar('histogram_quantile(0.95, sum by (le) (rate(qianwen_nuclio_function_request_duration_seconds_bucket{tenant="%s"}[5m])))' % tenant)
    functions = prom_vector('sum by (function) (rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m]))' % tenant, ["function"])
    errors = prom_vector('sum by (function) (rate(qianwen_nuclio_function_errors_total{tenant="%s"}[5m]))' % tenant, ["function"])
    running_pods = prom_vector('sum by (namespace) (kube_pod_status_phase{namespace=~"%s",phase="Running"})' % ns_re, ["namespace"])
    restarts = prom_vector('sum by (namespace,pod) (kube_pod_container_status_restarts_total{namespace=~"%s"})' % ns_re, ["namespace", "pod"])
    databases = []
    if TENANT_DB_NAMES:
        backends = {x["datname"]: x["value"] for x in prom_vector('pg:db:num_backends{datname=~"%s"}' % db_re, ["datname"])}
        xact = {x["datname"]: x["value"] for x in prom_vector('pg:db:xact_total_rate5m{datname=~"%s"}' % db_re, ["datname"])}
        size = {x["datname"]: x["value"] for x in prom_vector('pg:db:db_size{datname=~"%s"}' % db_re, ["datname"])}
        conn = {x["datname"]: x["value"] for x in prom_vector('pg:db:conn_usage{datname=~"%s"}' % db_re, ["datname"])}
        for name in TENANT_DB_NAMES:
            databases.append({
                "datname": name,
                "backends": backends.get(name, 0.0),
                "xactRate5m": xact.get(name, 0.0),
                "sizeBytes": size.get(name, 0.0),
                "connUsage": conn.get(name, 0.0),
            })
    return {
        "tenant": tenant,
        "namespace": TENANT,
        "namespaces": TENANT_NAMESPACES,
        "databases": TENANT_DB_NAMES,
        "metricsSource": METRICS_URL,
        "grafanaDashboard": TENANT_GRAFANA_DASHBOARD,
        "summary": {
            "functionCount": function_count,
            "requestRps5m": request_rps,
            "errorRps5m": error_rps,
            "functionP95Seconds5m": p95,
        },
        "functionsByRps": sorted(functions, key=lambda x: x["value"], reverse=True)[:20],
        "errorsByRps": sorted(errors, key=lambda x: x["value"], reverse=True)[:20],
        "runningPods": running_pods,
        "topRestarts": sorted(restarts, key=lambda x: x["value"], reverse=True)[:20],
        "databaseMetrics": databases,
    }


NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_k8s_name(value, field="name"):
    if not value or not isinstance(value, str) or len(value) > 63 or not NAME_RE.match(value):
        raise ValueError("%s must be a DNS-1123 name" % field)
    return value


def validate_email(value):
    if not value or not isinstance(value, str) or not EMAIL_RE.match(value):
        raise ValueError("email is invalid")
    return value.lower()


def dex_group_for_namespace(namespace):
    return "re8ch:%s-operators" % namespace


def rbac_group_name(dex_group):
    return dex_group if dex_group.startswith("oidc:") else "oidc:" + dex_group


def b64encode_text(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def b64decode_text(value):
    return base64.b64decode(value).decode("utf-8")


def registry_host(url=None):
    parsed = urllib.parse.urlparse(url or REGISTRY_PUBLIC)
    return parsed.netloc or parsed.path


def harbor_api_base():
    return HARBOR_API_URL.rstrip("/") + "/api/v2.0"


def harbor_auth_header():
    if not HARBOR_USERNAME or not HARBOR_PASSWORD:
        raise RuntimeError("Harbor credentials are not configured on Ops MCP")
    token = base64.b64encode(("%s:%s" % (HARBOR_USERNAME, HARBOR_PASSWORD)).encode("utf-8")).decode("ascii")
    return "Basic " + token


def harbor_request(method, path, body=None):
    data = None
    headers = {"Authorization": harbor_auth_header()}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(harbor_api_base() + path, data=data, method=method, headers=headers)
    context = ssl._create_unverified_context() if HARBOR_SKIP_TLS_VERIFY else None
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("harbor %s %s failed: %s %s" % (method, path, exc.code, raw))


def harbor_request_ignore(method, path, body=None, ignore_status=None):
    ignore_status = set(ignore_status or [])
    try:
        return harbor_request(method, path, body), None
    except RuntimeError as exc:
        text = str(exc)
        for status in ignore_status:
            if "failed: %s " % status in text:
                return {}, status
        raise


def harbor_quote(value):
    return urllib.parse.quote(value, safe="")


def harbor_get_project(project):
    matches = harbor_request("GET", "/projects?name=%s" % harbor_quote(project))
    for item in matches if isinstance(matches, list) else []:
        if item.get("name") == project:
            return item
    return None


def ensure_harbor_project(project):
    project = validate_k8s_name(project, "harbor project")
    existing = harbor_get_project(project)
    if existing:
        return {"action": "exists", "project": project, "projectID": existing.get("project_id")}
    payload = {"project_name": project, "metadata": {"public": "false"}, "storage_limit": -1}
    harbor_request_ignore("POST", "/projects", payload, ignore_status={409})
    created = harbor_get_project(project)
    return {"action": "created", "project": project, "projectID": created.get("project_id") if created else None}


def harbor_user_by_username(username):
    users = harbor_request("GET", "/users/search?username=%s" % harbor_quote(username))
    for user in users if isinstance(users, list) else []:
        if user.get("username") == username:
            return user
    return None


def normalize_harbor_username(username, email):
    value = (username or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip(".-_")
    if not value:
        raise ValueError("harbor username is required")
    return value[:64]


def ensure_harbor_user(email, username=None, password=None, namespace=None, create_user=True):
    email = validate_email(email)
    username = normalize_harbor_username(username, email)
    existing = harbor_user_by_username(username)
    secret_ref = None
    generated = False
    action = "exists"
    if not existing:
        if not create_user:
            raise ValueError("harbor user does not exist: %s" % username)
        if not password:
            password = secrets.token_urlsafe(24)
            generated = True
        payload = {
            "username": username,
            "email": email,
            "password": password,
            "realname": username,
            "comment": "managed by ops.re8ch.com",
        }
        harbor_request("POST", "/users", payload)
        existing = harbor_user_by_username(username)
        action = "created"
    if action == "created" and password and namespace:
        secret_name = "harbor-%s-web-login" % namespace
        upsert_plain_secret(namespace, secret_name, {
            "registry": registry_host(),
            "url": HARBOR_URL,
            "username": username,
            "email": email,
            "password": password,
        }, {"re8ch.com/tenant": namespace, "re8ch.com/secret-purpose": "harbor-web-login"})
        secret_ref = "%s/%s" % (namespace, secret_name)
    return {
        "username": username,
        "email": email,
        "userID": existing.get("user_id") if existing else None,
        "action": action,
        "generatedPassword": generated,
        "passwordSecret": secret_ref,
    }


def harbor_project_members(project):
    return harbor_request("GET", "/projects/%s/members" % harbor_quote(project))


def ensure_harbor_project_member(project, username, role_id=1):
    members = harbor_project_members(project)
    for member in members if isinstance(members, list) else []:
        if member.get("entity_type") == "u" and member.get("entity_name") == username:
            if int(member.get("role_id", 0)) == int(role_id):
                return {"action": "exists", "username": username, "roleID": role_id, "roleName": member.get("role_name")}
            harbor_request("PUT", "/projects/%s/members/%s" % (harbor_quote(project), member.get("id")), {"role_id": int(role_id)})
            return {"action": "updated", "username": username, "roleID": role_id}
    payload = {"role_id": int(role_id), "member_user": {"username": username}}
    harbor_request_ignore("POST", "/projects/%s/members" % harbor_quote(project), payload, ignore_status={409})
    return {"action": "created", "username": username, "roleID": role_id}


def paged_harbor(path):
    items = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        batch = harbor_request("GET", "%s%spage=%s&page_size=100" % (path, sep, page))
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def find_harbor_robot(name):
    for robot in paged_harbor("/robots"):
        if robot.get("name") == name or robot.get("name") == "robot$" + name:
            return robot
    return None


def create_harbor_project_admin_robot(project, robot_name):
    secret = secrets.token_urlsafe(48)
    payload = {
        "name": robot_name,
        "secret": secret,
        "description": "project admin robot for tenant project %s, managed by ops.re8ch.com" % project,
        "disable": False,
        "duration": -1,
        "level": "project",
        "permissions": [
            {
                "kind": "project",
                "namespace": project,
                "access": [
                    {"resource": "repository", "action": "pull"},
                    {"resource": "repository", "action": "push"},
                    {"resource": "repository", "action": "delete"},
                ],
            }
        ],
    }
    created = harbor_request("POST", "/robots", payload)
    username = str(created.get("name") or "robot$" + robot_name)
    token = str(created.get("token") or created.get("secret") or secret)
    if not token:
        raise RuntimeError("Harbor did not return a robot token for %s" % username)
    return username, token


def ensure_harbor_project_admin_robot(project, rotate=False):
    robot_name = "%s-admin-20260606" % project
    existing = find_harbor_robot(robot_name)
    if existing and not rotate:
        return {"action": "exists", "robotName": existing.get("name"), "tokenAvailable": False}
    if existing:
        harbor_request("DELETE", "/robots/%s" % existing.get("id"))
    username, token = create_harbor_project_admin_robot(project, robot_name)
    return {"action": "created", "robotName": username, "token": token, "tokenAvailable": True}


def dockerconfigjson(registry, username, password):
    auth = base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
    return {
        "auths": {
            registry: {
                "username": username,
                "password": password,
                "auth": auth,
            }
        }
    }


def upsert_secret_obj(namespace, name, secret_type, data, labels=None, annotations=None):
    obj = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "labels": labels or {"re8ch.com/tenant": namespace}, "annotations": annotations or {}},
        "type": secret_type,
        "data": data,
    }
    base = "/api/v1/namespaces/%s/secrets" % namespace
    return kube_apply(base, base + "/" + name, obj)


def upsert_plain_secret(namespace, name, values, labels=None):
    return upsert_secret_obj(namespace, name, "Opaque", {k: b64encode_text(str(v)) for k, v in values.items()}, labels=labels)


def upsert_dockerconfig_secret(namespace, name, registry, username, password, purpose):
    cfg = dockerconfigjson(registry, username, password)
    encoded = base64.b64encode(json.dumps(cfg, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return upsert_secret_obj(
        namespace,
        name,
        "kubernetes.io/dockerconfigjson",
        {".dockerconfigjson": encoded},
        labels={"re8ch.com/tenant": namespace, "re8ch.com/secret-purpose": purpose},
        annotations={"re8ch.com/registry": registry, "re8ch.com/robot": username},
    )


def patch_serviceaccount_pull_secret(namespace, serviceaccount, secret_name):
    try:
        current = kube_request("GET", "/api/v1/namespaces/%s/serviceaccounts/%s" % (namespace, serviceaccount))
    except KubeError as exc:
        if exc.status == 404:
            return {"serviceAccount": "%s/%s" % (namespace, serviceaccount), "action": "missing"}
        raise
    names = [x.get("name") for x in current.get("imagePullSecrets", []) if x.get("name")]
    if secret_name in names:
        return {"serviceAccount": "%s/%s" % (namespace, serviceaccount), "action": "exists"}
    patch = {"imagePullSecrets": [{"name": x} for x in names + [secret_name]]}
    kube_request("PATCH", "/api/v1/namespaces/%s/serviceaccounts/%s" % (namespace, serviceaccount), patch, "application/merge-patch+json")
    return {"serviceAccount": "%s/%s" % (namespace, serviceaccount), "action": "patched"}


def kube_apply(collection_path, item_path, obj):
    try:
        current = kube_request("GET", item_path)
        rv = current.get("metadata", {}).get("resourceVersion")
        if rv:
            obj.setdefault("metadata", {})["resourceVersion"] = rv
        return kube_request("PUT", item_path, obj)
    except KubeError as exc:
        if exc.status != 404:
            raise
        return kube_request("POST", collection_path, obj)


def ensure_namespace(namespace):
    namespace = validate_k8s_name(namespace, "namespace")
    item = "/api/v1/namespaces/%s" % namespace
    try:
        kube_request("GET", item)
        patch = {"metadata": {"labels": {"re8ch.com/tenant": namespace}}}
        return kube_request("PATCH", item, patch, "application/merge-patch+json")
    except KubeError as exc:
        if exc.status != 404:
            raise
    obj = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": {"re8ch.com/tenant": namespace}}}
    return kube_request("POST", "/api/v1/namespaces", obj)


def ensure_role(namespace, role_name, rules):
    obj = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": role_name, "namespace": namespace, "labels": {"re8ch.com/tenant": namespace}}, "rules": rules}
    base = "/apis/rbac.authorization.k8s.io/v1/namespaces/%s/roles" % namespace
    return kube_apply(base, base + "/" + role_name, obj)


def ensure_rolebinding(namespace, name, role_name, subjects, labels=None, annotations=None):
    obj = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": name, "namespace": namespace, "labels": labels or {"re8ch.com/tenant": namespace}, "annotations": annotations or {}},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role_name},
        "subjects": subjects,
    }
    base = "/apis/rbac.authorization.k8s.io/v1/namespaces/%s/rolebindings" % namespace
    return kube_apply(base, base + "/" + name, obj)


def ensure_serviceaccount(namespace, name):
    obj = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": name, "namespace": namespace, "labels": {"re8ch.com/tenant": namespace}}}
    base = "/api/v1/namespaces/%s/serviceaccounts" % namespace
    return kube_apply(base, base + "/" + name, obj)


def ensure_serviceaccount_token_secret(namespace, secret_name, serviceaccount):
    item = "/api/v1/namespaces/%s/secrets/%s" % (namespace, secret_name)
    try:
        return kube_request("GET", item)
    except KubeError as exc:
        if exc.status != 404:
            raise
    obj = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/service-account-token",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {"re8ch.com/tenant": namespace},
            "annotations": {"kubernetes.io/service-account.name": serviceaccount},
        },
    }
    return kube_request("POST", "/api/v1/namespaces/%s/secrets" % namespace, obj)


def ensure_clusterrole(name, rules):
    obj = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": name, "labels": {"re8ch.com/managed-by": "ops-mcp"}}, "rules": rules}
    base = "/apis/rbac.authorization.k8s.io/v1/clusterroles"
    return kube_apply(base, base + "/" + name, obj)


def ensure_clusterrolebinding(name, clusterrole, subjects, labels=None, annotations=None):
    obj = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": name, "labels": labels or {"re8ch.com/managed-by": "ops-mcp"}, "annotations": annotations or {}},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": clusterrole},
        "subjects": subjects,
    }
    base = "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings"
    return kube_apply(base, base + "/" + name, obj)


def tenant_namespace_api_groups():
    return ["", "apps", "batch", "autoscaling", "networking.k8s.io", "discovery.k8s.io", "jobset.x-k8s.io", "nuclio.io"]


def tenant_operator_rules():
    return [{"apiGroups": tenant_namespace_api_groups(), "resources": ["*"], "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]}]


def tenant_viewer_rules():
    return [{"apiGroups": tenant_namespace_api_groups(), "resources": ["*"], "verbs": ["get", "list", "watch"]}]


def tenant_ui_cluster_viewer_rules():
    return [
        {"apiGroups": [""], "resources": ["namespaces", "nodes", "persistentvolumes"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": ["metrics.k8s.io"], "resources": ["nodes"], "verbs": ["get", "list"]},
        {"apiGroups": ["networking.k8s.io"], "resources": ["ingressclasses"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": ["storage.k8s.io"], "resources": ["storageclasses", "volumeattributesclasses"], "verbs": ["get", "list", "watch"]},
    ]


def ensure_tenant_access(namespace, group=None, serviceaccount=None, create_namespace=True):
    namespace = validate_k8s_name(namespace, "namespace")
    group = group or dex_group_for_namespace(namespace)
    serviceaccount = serviceaccount or "%s-kubectl-operator" % namespace
    validate_k8s_name(serviceaccount, "serviceAccount")
    token_secret = "%s-permanent-token" % serviceaccount
    if create_namespace:
        ensure_namespace(namespace)
    ensure_role(namespace, "tenant-operator", tenant_operator_rules())
    ensure_role(namespace, "tenant-viewer", tenant_viewer_rules())
    ensure_serviceaccount(namespace, serviceaccount)
    ensure_serviceaccount_token_secret(namespace, token_secret, serviceaccount)
    human_rb = "%s-operators-tenant-operator-oidc" % namespace
    robot_rb = "%s-kubectl-operator" % namespace
    ensure_rolebinding(namespace, human_rb, "tenant-operator", [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": rbac_group_name(group)}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "human-oidc-group"}, annotations={"re8ch.com/pairs-with-serviceaccount": "%s/%s" % (namespace, serviceaccount)})
    ensure_rolebinding(namespace, robot_rb, "tenant-operator", [{"kind": "ServiceAccount", "name": serviceaccount, "namespace": namespace}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "robot-serviceaccount"}, annotations={"re8ch.com/pairs-with-oidc-group": rbac_group_name(group)})
    ensure_clusterrole("tenant-ui-cluster-viewer", tenant_ui_cluster_viewer_rules())
    human_crb = "%s-headlamp-ui-cluster-viewer-oidc" % namespace
    robot_crb = "%s-kubectl-operator-ui-cluster-viewer" % namespace
    ensure_clusterrolebinding(human_crb, "tenant-ui-cluster-viewer", [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": rbac_group_name(group)}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "cluster-readonly", "re8ch.com/principal-type": "human-oidc-group"})
    ensure_clusterrolebinding(robot_crb, "tenant-ui-cluster-viewer", [{"kind": "ServiceAccount", "name": serviceaccount, "namespace": namespace}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "cluster-readonly", "re8ch.com/principal-type": "robot-serviceaccount"})
    return {"namespace": namespace, "dexGroup": group, "rbacGroup": rbac_group_name(group), "serviceAccount": "%s/%s" % (namespace, serviceaccount), "tokenSecret": "%s/%s" % (namespace, token_secret), "role": "%s/tenant-operator" % namespace, "humanRoleBinding": "%s/%s" % (namespace, human_rb), "robotRoleBinding": "%s/%s" % (namespace, robot_rb), "clusterRole": "tenant-ui-cluster-viewer", "humanClusterRoleBinding": human_crb, "robotClusterRoleBinding": robot_crb}



def user_binding_name(email):
    local = email.split("@", 1)[0].lower()
    safe = re.sub(r"[^a-z0-9-]+", "-", local).strip("-") or "user"
    return safe[:40]


def ensure_user_namespace_access(email, namespace, role_name="tenant-operator"):
    email = validate_email(email)
    namespace = validate_k8s_name(namespace, "namespace")
    short = user_binding_name(email)
    rb = "%s-user-%s-tenant-operator" % (namespace, short)
    crb = "%s-user-%s-ui-cluster-viewer" % (namespace, short)
    user_subject = {"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": "oidc:" + email}
    ensure_rolebinding(
        namespace,
        rb,
        role_name,
        [user_subject],
        labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "human-oidc-user"},
        annotations={"re8ch.com/email": email, "re8ch.com/reason": "Dex local password users may not emit groups; bind the OIDC user directly."},
    )
    ensure_clusterrolebinding(
        crb,
        "tenant-ui-cluster-viewer",
        [user_subject],
        labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "cluster-readonly", "re8ch.com/principal-type": "human-oidc-user"},
        annotations={"re8ch.com/email": email, "re8ch.com/reason": "Headlamp UI needs namespace/node read access for Dex local users."},
    )
    return {"user": "oidc:" + email, "roleBinding": "%s/%s" % (namespace, rb), "clusterRoleBinding": crb}

def split_static_passwords(config_text):
    lines = config_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "staticPasswords:":
            start = i
            break
    if start is None:
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == "connectors:":
                insert_at = i
                break
        return lines[:insert_at], ["staticPasswords:"], lines[insert_at:]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith(" ") and not lines[i].startswith("\t"):
            end = i
            break
    return lines[:start], lines[start:end], lines[end:]


def yaml_value(line):
    value = line.split(":", 1)[1].strip()
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    return value


def parse_static_password_users(block):
    users = []
    current = None
    in_groups = False
    for line in block[1:]:
        if line.startswith("  - email:"):
            if current:
                users.append(current)
            current = {"email": yaml_value(line), "hash": "", "username": "", "userID": "", "groups": []}
            in_groups = False
        elif current and line.startswith("    hash:"):
            current["hash"] = yaml_value(line)
            in_groups = False
        elif current and line.startswith("    username:"):
            current["username"] = yaml_value(line)
            in_groups = False
        elif current and line.startswith("    userID:"):
            current["userID"] = yaml_value(line)
            in_groups = False
        elif current and line.strip() == "groups:":
            in_groups = True
        elif current and in_groups and line.startswith("      - "):
            current["groups"].append(line.split("- ", 1)[1].strip())
    if current:
        users.append(current)
    return users


def render_static_password_users(users):
    out = ["staticPasswords:"]
    for user in users:
        out.append("  - email: %s" % user["email"])
        out.append("    hash: %s" % user["hash"])
        out.append("    username: %s" % user["username"])
        out.append("    userID: %s" % user["userID"])
        out.append("    groups:")
        for group in sorted(set(user.get("groups", []))):
            out.append("      - %s" % group)
    return out


def load_dex_config_secret():
    secret = kube_request("GET", "/api/v1/namespaces/dex/secrets/dex-config")
    encoded = secret.get("data", {}).get("config.yaml")
    if not encoded:
        raise RuntimeError("dex-config secret has no config.yaml")
    return secret, b64decode_text(encoded)


def save_dex_config_secret(secret, config_text):
    secret.setdefault("data", {})["config.yaml"] = b64encode_text(config_text)
    return kube_request("PUT", "/api/v1/namespaces/dex/secrets/dex-config", secret)


def restart_dex():
    patch = {"spec": {"template": {"metadata": {"annotations": {"ops.re8ch.com/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}}}}
    return kube_request("PATCH", "/apis/apps/v1/namespaces/dex/deployments/dex", patch, "application/strategic-merge-patch+json")


def list_dex_users():
    _, config_text = load_dex_config_secret()
    _, block, _ = split_static_passwords(config_text)
    users = parse_static_password_users(block)
    return {"count": len(users), "users": [{"email": u["email"], "username": u["username"], "userID": u["userID"], "groups": sorted(set(u.get("groups", [])))} for u in users]}


def write_dex_user(email, username=None, user_id=None, password_hash=None, groups=None, create=True, update_existing=True):
    email = validate_email(email)
    groups = sorted(set(groups or []))
    if password_hash and not password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        raise ValueError("passwordHash must be a bcrypt hash such as $2a$10$...")
    secret, config_text = load_dex_config_secret()
    before, block, after = split_static_passwords(config_text)
    users = parse_static_password_users(block)
    found = None
    for user in users:
        if user.get("email", "").lower() == email:
            found = user
            break
    if found:
        if not update_existing:
            raise ValueError("dex user already exists: %s" % email)
        if username:
            found["username"] = username
        if user_id:
            found["userID"] = user_id
        if password_hash:
            found["hash"] = password_hash
        found["groups"] = sorted(set(found.get("groups", []) + groups))
        result_user = found
        action = "updated"
    else:
        if not create:
            raise ValueError("dex user does not exist: %s" % email)
        if not password_hash:
            raise ValueError("passwordHash is required when creating a new Dex static user")
        username = username or email.split("@", 1)[0]
        user_id = user_id or username
        result_user = {"email": email, "hash": password_hash, "username": username, "userID": user_id, "groups": groups}
        users.append(result_user)
        action = "created"
    new_config = "\n".join(before + render_static_password_users(users) + after) + "\n"
    save_dex_config_secret(secret, new_config)
    restart_dex()
    return {"action": action, "email": email, "username": result_user.get("username"), "userID": result_user.get("userID"), "groups": sorted(set(result_user.get("groups", [])))}


def create_dex_user(args):
    args = args or {}
    namespace = args.get("namespace")
    group = args.get("group")
    groups = list(args.get("groups") or [])
    tenant = None
    if namespace:
        namespace = validate_k8s_name(namespace, "namespace")
        group = group or dex_group_for_namespace(namespace)
        groups.append(group)
        tenant = ensure_tenant_access(namespace, group=group, create_namespace=bool(args.get("createNamespace", True)))
    user = write_dex_user(email=args.get("email"), username=args.get("username"), user_id=args.get("userID"), password_hash=args.get("passwordHash"), groups=groups, create=True, update_existing=bool(args.get("updateExisting", False)))
    direct_user_access = ensure_user_namespace_access(user["email"], namespace) if namespace else None
    harbor = None
    if namespace and bool(args.get("ensureHarbor", False)):
        harbor = ensure_harbor_tenant_identity({
            "namespace": namespace,
            "email": user["email"],
            "harborPassword": args.get("harborPassword"),
            "rotateRobot": bool(args.get("rotateHarborRobot", False)),
        })
    return {"user": user, "tenantAccess": tenant, "directUserAccess": direct_user_access, "harbor": harbor, "note": "Dex deployment was restarted. New login may take a short moment."}


def grant_user_namespace(args):
    args = args or {}
    namespace = validate_k8s_name(args.get("namespace"), "namespace")
    group = args.get("group") or dex_group_for_namespace(namespace)
    tenant = ensure_tenant_access(namespace, group=group, create_namespace=bool(args.get("createNamespace", True)))
    user = write_dex_user(email=args.get("email"), groups=[group], create=False, update_existing=True)
    direct_user_access = ensure_user_namespace_access(user["email"], namespace)
    return {"user": user, "tenantAccess": tenant, "directUserAccess": direct_user_access, "note": "Dex deployment was restarted. Existing sessions may need re-login to receive new groups."}


def ensure_harbor_tenant_identity(args):
    args = args or {}
    namespace = validate_k8s_name(args.get("namespace") or args.get("tenant"), "namespace")
    project = validate_k8s_name(args.get("project") or namespace, "harbor project")
    if project != namespace and not bool(args.get("allowDifferentProject", False)):
        raise ValueError("project must match namespace unless allowDifferentProject=true")
    email = validate_email(args.get("email") or ("%s@re8ch.com" % namespace))
    username = normalize_harbor_username(args.get("username") or namespace, email)
    if username != namespace and not bool(args.get("allowDifferentUsername", False)):
        raise ValueError("harbor username must match namespace unless allowDifferentUsername=true")
    ensure_namespace(namespace)
    project_result = ensure_harbor_project(project)
    web_user = ensure_harbor_user(
        email=email,
        username=username,
        password=args.get("harborPassword"),
        namespace=namespace,
        create_user=bool(args.get("createUser", True)),
    )
    member = ensure_harbor_project_member(project, username, int(args.get("roleID") or 1))
    rotate_robot = bool(args.get("rotateRobot", False))
    robot = ensure_harbor_project_admin_robot(project, rotate=rotate_robot)
    robot_secret = "harbor-%s-admin" % project
    if robot.get("token"):
        upsert_dockerconfig_secret(namespace, robot_secret, registry_host(), robot["robotName"], robot["token"], "harbor-project-admin")
        if REGISTRY_ALT:
            upsert_dockerconfig_secret(namespace, robot_secret + "-ynzs", registry_host(REGISTRY_ALT), robot["robotName"], robot["token"], "harbor-project-admin-alt")
    patched = [
        patch_serviceaccount_pull_secret(namespace, "default", robot_secret),
        patch_serviceaccount_pull_secret(namespace, "%s-kubectl-operator" % namespace, robot_secret),
    ]
    return {
        "namespace": namespace,
        "project": project_result,
        "webLogin": {
            "url": HARBOR_URL,
            "username": web_user["username"],
            "email": web_user["email"],
            "passwordSecret": web_user.get("passwordSecret"),
            "generatedPassword": web_user.get("generatedPassword", False),
        },
        "projectMember": member,
        "robot": {
            "username": robot.get("robotName"),
            "secret": "%s/%s" % (namespace, robot_secret),
            "alternateSecret": "%s/%s-ynzs" % (namespace, robot_secret) if REGISTRY_ALT and robot.get("token") else None,
            "tokenReturned": False,
            "note": "Robot token is stored in Kubernetes dockerconfigjson secret and is not returned by this API.",
        },
        "serviceAccounts": patched,
        "imageExamples": [
            "%s/%s/<image>:<tag>" % (registry_host(), project),
            "%s/%s/<image>:<tag>" % (registry_host(REGISTRY_ALT), project) if REGISTRY_ALT else None,
        ],
    }


def get_dex_hash_instructions():
    return {
        "reason": "Dex staticPasswords require bcrypt hashes. Ops MCP intentionally does not accept plaintext password hashing in the public API.",
        "generateWithHtpasswd": "htpasswd -bnBC 10 \"\" '<password>' | tr -d ':\\n'",
        "generateWithDocker": "docker run --rm httpd:2.4-alpine htpasswd -bnBC 10 \"\" '<password>' | tr -d ':\\n'",
        "thenCall": "create_dex_user with passwordHash=$2a$10$...",
    }


def get_tenancy_admin_workflow():
    return {
        "purpose": "Standardize Headlamp/Dex human access, kubectl robot access, and Harbor registry identity for a tenant namespace.",
        "auth": "Use Ops MCP admin token. Normal Ops token cannot call these tools.",
        "principle": "Human OIDC group, Dex local OIDC user, and robot ServiceAccount are reconciled to tenant operator permissions, plus the same read-only cluster UI view.",
        "steps": [
            {"step": 1, "tool": "get_dex_hash_instructions", "when": "Before creating a new Dex static user.", "result": "Generate passwordHash with htpasswd/docker; do not send plaintext password to Ops MCP."},
            {"step": 2, "tool": "ensure_tenant_access", "when": "Create or repair namespace-level access without changing Dex users.", "arguments": {"namespace": "ledger"}, "result": "Ensures namespace, tenant-operator/viewer roles, OIDC group RoleBinding, robot ServiceAccount token, and cluster read-only bindings."},
            {"step": 3, "tool": "create_dex_user", "when": "Create a new human login and grant namespace access in one call.", "arguments": {"email": "alice@example.com", "username": "alice", "userID": "alice", "passwordHash": "$2a$10$...", "namespace": "ledger"}, "result": "Adds static Dex user to re8ch:ledger-operators, binds oidc:<email> directly for Dex local login, reconciles tenant RBAC, and restarts Dex."},
            {"step": 4, "tool": "grant_user_namespace", "when": "Existing Dex user needs access to another namespace.", "arguments": {"email": "alice@example.com", "namespace": "ledger"}, "result": "Adds re8ch:ledger-operators to existing Dex user, binds oidc:<email> directly for Dex local login, reconciles tenant RBAC, and restarts Dex."},
            {"step": 5, "tool": "ensure_harbor_tenant", "when": "Create or repair the matching Harbor project, web login, project admin membership, robot token secret, and imagePullSecrets.", "arguments": {"namespace": "ledger", "email": "ledger@re8ch.com"}, "result": "Ensures Harbor project ledger, Harbor username ledger, K8s secret ledger/harbor-ledger-admin, and registry image path registry.re8ch.com/ledger/<image>:<tag>."},
            {"step": 6, "verify": ["kubectl auth can-i --as=oidc:<email> --as-group=oidc:re8ch:<namespace>-operators -n <namespace> create deployments.apps", "kubectl auth can-i --as=system:serviceaccount:<namespace>:<namespace>-kubectl-operator -n <namespace> create deployments.apps", "kubectl auth can-i --as=oidc:<email> --as-group=oidc:re8ch:<namespace>-operators -n other-namespace get pods should be no", "kubectl -n <namespace> get secret harbor-<namespace>-admin"]},
        ],
        "createdObjects": ["namespace/<namespace>", "role/<namespace>/tenant-operator", "role/<namespace>/tenant-viewer", "rolebinding/<namespace>/<namespace>-operators-tenant-operator-oidc", "serviceaccount/<namespace>/<namespace>-kubectl-operator", "secret/<namespace>/<namespace>-kubectl-operator-permanent-token", "rolebinding/<namespace>/<namespace>-kubectl-operator", "clusterrole/tenant-ui-cluster-viewer", "clusterrolebinding/<namespace>-headlamp-ui-cluster-viewer-oidc", "clusterrolebinding/<namespace>-kubectl-operator-ui-cluster-viewer", "harbor project/<namespace>", "harbor user/<namespace>", "secret/<namespace>/harbor-<namespace>-admin"],
        "safety": ["The workflow is idempotent and uses apply-like create/update behavior.", "It does not delete existing namespace workloads or business secrets.", "Dex user creation requires passwordHash, not plaintext password.", "Harbor robot tokens are stored in Kubernetes secrets and are not returned by the API.", "Dex changes restart the Dex deployment; existing users may need to log in again to receive new groups."],
    }

def mcp_tools(include_admin=False):
    def schema(props=None, required=None):
        return {"type": "object", "properties": props or {}, "required": required or []}
    tools = [
        {"name": "get_usage_guide", "description": "Return the Ops usage guide.", "inputSchema": schema()},
        {"name": "get_tenant_context", "description": "Return qwen tenant endpoints and safety boundaries.", "inputSchema": schema()},
        {"name": "get_kubeconfig", "description": "Return qwen operator kubeconfig for CLI automation.", "inputSchema": schema()},
        {"name": "list_k8s_resources", "description": "List qwen Kubernetes resources or read-only nodes.", "inputSchema": schema({"kind": {"type": "string", "default": "pods"}})},
        {"name": "restart_deployment", "description": "Restart one Deployment in qwen namespace.", "inputSchema": schema({"deployment": {"type": "string"}}, ["deployment"])},
        {"name": "get_registry_info", "description": "Return Harbor registry information for qwen images.", "inputSchema": schema()},
        {"name": "get_grafana_links", "description": "Return Grafana links and intended usage.", "inputSchema": schema()},
        {"name": "get_grafana_access", "description": "Return the tenant Grafana service account token and API usage hints for AI agents. The token is Viewer-scoped to the tenant Grafana org.", "inputSchema": schema()},
        {"name": "get_tenant_metrics_summary", "description": "Return tenant-scoped function, namespace, and database metrics. The tenant filters are enforced by Ops MCP configuration; arbitrary PromQL is not accepted.", "inputSchema": schema()},
    ]
    if include_admin:
        tools.extend([
            {"name": "list_dex_users", "description": "Admin: list Dex static users without password hashes.", "inputSchema": schema()},
            {"name": "get_tenancy_admin_workflow", "description": "Admin: explain the standard tenant/user access workflow for Headlamp, Dex OIDC, RBAC, and robot kubeconfig.", "inputSchema": schema()},
            {"name": "get_dex_hash_instructions", "description": "Admin: return commands for generating Dex bcrypt passwordHash.", "inputSchema": schema()},
            {"name": "ensure_tenant_access", "description": "Admin: create namespace, tenant roles, OIDC group RoleBinding, robot ServiceAccount token, and cluster read-only bindings.", "inputSchema": schema({"namespace": {"type": "string"}, "group": {"type": "string"}, "serviceAccount": {"type": "string"}, "createNamespace": {"type": "boolean", "default": True}}, ["namespace"])},
            {"name": "create_dex_user", "description": "Admin: create a Dex static user and optionally grant namespace operator access. Requires passwordHash, not plaintext password. Can also reconcile the matching Harbor tenant when ensureHarbor=true.", "inputSchema": schema({"email": {"type": "string"}, "username": {"type": "string"}, "userID": {"type": "string"}, "passwordHash": {"type": "string"}, "namespace": {"type": "string"}, "group": {"type": "string"}, "groups": {"type": "array", "items": {"type": "string"}}, "createNamespace": {"type": "boolean", "default": True}, "updateExisting": {"type": "boolean", "default": False}, "ensureHarbor": {"type": "boolean", "default": False}, "harborPassword": {"type": "string"}, "rotateHarborRobot": {"type": "boolean", "default": False}}, ["email", "passwordHash"])},
            {"name": "grant_user_namespace", "description": "Admin: grant an existing Dex static user operator access to a namespace and reconcile tenant RBAC.", "inputSchema": schema({"email": {"type": "string"}, "namespace": {"type": "string"}, "group": {"type": "string"}, "createNamespace": {"type": "boolean", "default": True}}, ["email", "namespace"])},
            {"name": "ensure_harbor_tenant", "description": "Admin: create or repair the matching Harbor project, Harbor web user, projectAdmin membership, project admin robot, and Kubernetes image secret. Defaults require namespace/project/username to be the same.", "inputSchema": schema({"namespace": {"type": "string"}, "tenant": {"type": "string"}, "project": {"type": "string"}, "email": {"type": "string"}, "username": {"type": "string"}, "harborPassword": {"type": "string"}, "createUser": {"type": "boolean", "default": True}, "roleID": {"type": "integer", "default": 1}, "rotateRobot": {"type": "boolean", "default": False}, "allowDifferentProject": {"type": "boolean", "default": False}, "allowDifferentUsername": {"type": "boolean", "default": False}}, [])},
        ])
    return tools

def call_tool(name, args, admin=False):
    args = args or {}
    if name == "get_usage_guide":
        return GUIDE
    if name == "get_tenant_context":
        return tenant_context()
    if name == "get_kubeconfig":
        return build_kubeconfig()
    if name == "list_k8s_resources":
        return list_resources(args.get("kind", "pods"))
    if name == "restart_deployment":
        return restart_deployment(args.get("deployment"))
    if name == "get_registry_info":
        return registry_info()
    if name == "get_grafana_links":
        return grafana_links()
    if name == "get_grafana_access":
        return grafana_access()
    if name == "get_tenant_metrics_summary":
        return get_tenant_metrics_summary()
    admin_tools = {"list_dex_users", "get_tenancy_admin_workflow", "get_dex_hash_instructions", "ensure_tenant_access", "create_dex_user", "grant_user_namespace", "ensure_harbor_tenant"}
    if name in admin_tools and not admin:
        raise PermissionError("admin token required for tool: %s" % name)
    if name == "list_dex_users":
        return list_dex_users()
    if name == "get_tenancy_admin_workflow":
        return get_tenancy_admin_workflow()
    if name == "get_dex_hash_instructions":
        return get_dex_hash_instructions()
    if name == "ensure_tenant_access":
        return ensure_tenant_access(args.get("namespace"), group=args.get("group"), serviceaccount=args.get("serviceAccount"), create_namespace=bool(args.get("createNamespace", True)))
    if name == "create_dex_user":
        return create_dex_user(args)
    if name == "grant_user_namespace":
        return grant_user_namespace(args)
    if name == "ensure_harbor_tenant":
        return ensure_harbor_tenant_identity(args)
    raise ValueError("unknown tool: %s" % name)

class Handler(BaseHTTPRequestHandler):
    server_version = "ops-mcp/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def presented_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return self.headers.get("X-Ops-Admin-Token") or self.headers.get("X-Ops-Token", "")

    def authorized(self):
        token = self.presented_token()
        return bool(token) and token in [OPS_TOKEN, OPS_ADMIN_TOKEN]

    def admin_authorized(self):
        token = self.presented_token()
        return bool(OPS_ADMIN_TOKEN) and token == OPS_ADMIN_TOKEN

    def send_json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_text(self, text, status=200):
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def require_auth(self):
        if self.authorized():
            return True
        self.send_json({"error": "unauthorized", "hint": "send Authorization: Bearer <ops-token>"}, 401)
        return False

    def require_admin(self):
        if self.admin_authorized():
            return True
        self.send_json({"error": "forbidden", "hint": "admin token required"}, 403)
        return False

    def do_HEAD(self):
        if self.path.startswith("/api/") or self.path == "/mcp":
            if not self.authorized():
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization,X-Ops-Token,X-Ops-Admin-Token,Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/healthz":
                self.send_json({"ok": True, "tenant": TENANT})
            elif parsed.path == "/":
                self.send_text(GUIDE)
            elif parsed.path == "/api/me":
                if self.require_auth():
                    self.send_json(tenant_context())
            elif parsed.path == "/api/kubeconfig":
                if self.require_auth():
                    self.send_text(build_kubeconfig())
            elif parsed.path == "/api/resources":
                if self.require_auth():
                    params = urllib.parse.parse_qs(parsed.query)
                    kind = params.get("kind", ["pods"])[0]
                    self.send_json(list_resources(kind))
            elif parsed.path == "/api/registry/info":
                if self.require_auth():
                    self.send_json(registry_info())
            elif parsed.path == "/api/grafana/links":
                if self.require_auth():
                    self.send_json(grafana_links())
            elif parsed.path == "/api/grafana/access":
                if self.require_auth():
                    self.send_json(grafana_access())
            elif parsed.path == "/api/metrics/summary":
                if self.require_auth():
                    self.send_json(get_tenant_metrics_summary())
            elif parsed.path == "/api/admin/dex-users":
                if self.require_admin():
                    self.send_json(list_dex_users())
            elif parsed.path == "/api/admin/dex-hash-instructions":
                if self.require_admin():
                    self.send_json(get_dex_hash_instructions())
            elif parsed.path == "/mcp":
                if self.require_auth():
                    self.send_json({"name": "ops-mcp", "transport": "http-jsonrpc", "endpoint": "/mcp"})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self.require_auth():
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
            if parsed.path == "/api/restart":
                self.send_json(restart_deployment(body.get("deployment")))
            elif parsed.path == "/api/admin/ensure-tenant-access":
                if self.require_admin():
                    self.send_json(ensure_tenant_access(body.get("namespace"), group=body.get("group"), serviceaccount=body.get("serviceAccount"), create_namespace=bool(body.get("createNamespace", True))))
            elif parsed.path == "/api/admin/create-dex-user":
                if self.require_admin():
                    self.send_json(create_dex_user(body))
            elif parsed.path == "/api/admin/grant-user-namespace":
                if self.require_admin():
                    self.send_json(grant_user_namespace(body))
            elif parsed.path == "/api/admin/ensure-harbor-tenant":
                if self.require_admin():
                    self.send_json(ensure_harbor_tenant_identity(body))
            elif parsed.path == "/mcp":
                self.handle_mcp(body)
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def handle_mcp(self, req):
        method = req.get("method")
        request_id = req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "re8ch-ops-mcp", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": mcp_tools(include_admin=self.admin_authorized())}
        elif method == "tools/call":
            params = req.get("params", {})
            value = call_tool(params.get("name"), params.get("arguments", {}), admin=self.admin_authorized())
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            result = {"content": [{"type": "text", "text": text}]}
        else:
            self.send_json({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
            return
        self.send_json({"jsonrpc": "2.0", "id": request_id, "result": result})

if __name__ == "__main__":
    print("ops-mcp listening on %s:%s for tenant=%s" % (LISTEN_HOST, LISTEN_PORT, TENANT), flush=True)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
