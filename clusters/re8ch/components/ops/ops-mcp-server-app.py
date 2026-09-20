import base64
import concurrent.futures
import contextvars
import hashlib
import hmac
import json
import os
import ipaddress
import re
import secrets
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPS_TOOL_PROFILE = os.getenv("OPS_TOOL_PROFILE", "legacy").lower()
DEFAULT_TENANT = "qwen" if OPS_TOOL_PROFILE == "legacy" else "platform"
TENANT = os.getenv("TENANT_NAMESPACE", DEFAULT_TENANT)
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8088"))
OPS_TOKEN = os.getenv("OPS_TOKEN", "")
OPS_ADMIN_TOKEN = os.getenv("OPS_ADMIN_TOKEN", "")
OPS_AUTH_SECRET_NAMESPACE = os.getenv("OPS_AUTH_SECRET_NAMESPACE", "ops")
OPS_AUTH_SECRET_NAME = os.getenv("OPS_AUTH_SECRET_NAME", "ops-mcp-auth")
TRUST_LOOPBACK_PROXY = os.getenv("TRUST_LOOPBACK_PROXY", "false").lower() == "true"
TRUST_PROXY_CIDRS = tuple(
    ipaddress.ip_network(value.strip())
    for value in os.getenv("TRUST_PROXY_CIDRS", "").split(",")
    if value.strip()
)
KUBE_SERVER_PUBLIC = os.getenv("KUBE_SERVER_PUBLIC", "https://192.0.2.176:6443")
REGISTRY_PUBLIC = os.getenv("REGISTRY_PUBLIC", "https://registry.re8ch.com")
REGISTRY_ALT = os.getenv("REGISTRY_ALT", "https://registry.ynzs.com")
GRAFANA_PUBLIC = os.getenv("GRAFANA_PUBLIC", "https://grafana.re8ch.com")
HEADLAMP_PUBLIC = os.getenv("HEADLAMP_PUBLIC", "https://headlamp.re8ch.com")
TOOLHIVE_REGISTRY_URL = os.getenv(
    "TOOLHIVE_REGISTRY_URL",
    "http://toolhive-registry-server.toolhive-system.svc.cluster.local:8080/v1/registries",
)
CLOUDBEAVER_PUBLIC = os.getenv("CLOUDBEAVER_PUBLIC", "https://dbgui.re8ch.com")
HARBOR_HEALTH_URL = os.getenv(
    "HARBOR_HEALTH_URL",
    "http://core.harbor.svc.cluster.local:8080/api/v2.0/health",
)
GRAFANA_INTERNAL = os.getenv("GRAFANA_INTERNAL", "http://192.0.2.192:3001/ui")
HARBOR_URL = os.getenv("HARBOR_URL", REGISTRY_PUBLIC).rstrip("/")
HARBOR_API_URL = os.getenv("HARBOR_API_URL", HARBOR_URL).rstrip("/")
HARBOR_SKIP_TLS_VERIFY = os.getenv("HARBOR_SKIP_TLS_VERIFY", "").lower() in ("1", "true", "yes")
HARBOR_USERNAME = os.getenv("HARBOR_USERNAME", "")
HARBOR_PASSWORD = os.getenv("HARBOR_PASSWORD", "")
AUTHENTIK_API_URL = os.getenv(
    "AUTHENTIK_API_URL", "http://authentik-server.auth.svc.cluster.local/api/v3"
).rstrip("/")
AUTHENTIK_API_TOKEN = os.getenv("AUTHENTIK_API_TOKEN", "")
TENANT_HARBOR_PROJECT = os.getenv("TENANT_HARBOR_PROJECT", TENANT)
TENANT_HARBOR_SECRET = os.getenv("TENANT_HARBOR_SECRET", "harbor-%s-admin" % TENANT_HARBOR_PROJECT)
TENANT_HARBOR_ROBOT = os.getenv("TENANT_HARBOR_ROBOT", "robot$%s+%s-admin-20260606" % (TENANT_HARBOR_PROJECT, TENANT_HARBOR_PROJECT))
METRICS_URL = os.getenv("METRICS_URL", "http://127.0.0.1:30428").rstrip("/")
TENANT_METRICS_TENANT = os.getenv("TENANT_METRICS_TENANT", TENANT_HARBOR_PROJECT)
TENANT_NAMESPACES = [x.strip() for x in os.getenv("TENANT_NAMESPACES", TENANT if OPS_TOOL_PROFILE == "legacy" else "").split(",") if x.strip()]
TENANT_DB_NAMES = [x.strip() for x in os.getenv("TENANT_DB_NAMES", "").split(",") if x.strip()]
TENANT_GRAFANA_DASHBOARD = os.getenv("TENANT_GRAFANA_DASHBOARD", "")
TENANT_GRAFANA_TOKEN_SECRET = os.getenv("TENANT_GRAFANA_TOKEN_SECRET", "grafana-%s-viewer" % TENANT_METRICS_TENANT)
GRAFANA_RENDER_PRESETS = {
    "traffic": {"panelId": "7", "title": "Function Requests by Function", "width": 1000, "height": 500},
    "errors": {"panelId": "8", "title": "Function Errors by Function", "width": 1000, "height": 500},
    "latency": {"panelId": "9", "title": "Function P95 by Function", "width": 1200, "height": 520},
    "pods": {"panelId": "13", "title": "Pod Placement", "width": 1100, "height": 520},
    "deployments": {"panelId": "15", "title": "Deployment Unavailable Replicas", "width": 1000, "height": 460},
    "db": {"panelId": "21", "title": "DB Transaction Rate", "width": 1000, "height": 500},
    "pg-lag": {"panelId": "32", "title": "PG Lag Seconds", "width": 1000, "height": 420},
    "pg-xact": {"panelId": "37", "title": "PG Instance Xact Rate", "width": 1000, "height": 500},
    "k3s-nodes": {"panelId": "45", "title": "K3s Node Ready", "width": 1000, "height": 460},
}
ACTIVE_TENANT = contextvars.ContextVar("active_tenant", default=TENANT)
SERVICE_ACCOUNT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
PG_SERVICE_ROUTES = {
    "writeService": {"host": "pgwrite.zt.re8ch.com", "port": "5433", "purpose": "HA primary/write service; recommended for migrations, admin SQL, and direct runtime writes."},
    "readService": {"host": "pgread-sh.zt.re8ch.com", "port": "5434", "purpose": "HA replica/read service in Shanghai when healthy."},
    "readServiceGz": {"host": "pgread-gz.zt.re8ch.com", "port": "5434", "purpose": "HA replica/read service in Guangzhou when healthy."},
    "poolRw": {"host": "pgwrite.zt.re8ch.com", "port": "6432", "purpose": "PgBouncer pooled connection entry for high-churn app clients."},
    "instancePrimaryName": {"host": "pgwrite.zt.re8ch.com", "port": "5432", "purpose": "Direct PostgreSQL instance port. Use only when a GUI/JDBC tool or a DBA intentionally targets an instance; it is not the preferred HA write route."},
    "postgrestGatewayShanghaiWrite": {"url": "http://192.0.2.89:3080", "purpose": "PostgREST/HA gateway near Shanghai primary path; use for HTTP CRUD from functions."},
    "postgrestGatewayShanghaiRead": {"url": "http://192.0.2.112:3080", "purpose": "PostgREST/HA gateway near Shanghai read path."},
    "postgrestGatewayGuangzhouRead": {"url": "http://192.0.2.251:3080", "purpose": "PostgREST/HA gateway near Guangzhou read path and current leader region when applicable."},
}
KUBE_API = "https://%s:%s" % (
    os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
    os.getenv("KUBERNETES_SERVICE_PORT", "443"),
)

PLATFORM_NAMESPACE_DOMAINS = {
    "kube-system": ("cluster-control", "Headlamp, Hubble, Grafana"),
    "cilium-secrets": ("cluster-control", "Headlamp"),
    "opentelemetry-operator": ("observability-control", "Grafana"),
    "qianwen-ops": ("observability-control", "Grafana, Hyperglass"),
    "observability-ai": ("observability-control", "Grafana, Robusta"),
    "opencost": ("observability-data", "OpenCost, Grafana"),
    "ops": ("platform-control", "Headlamp, CloudBeaver"),
    "toolhive-system": ("tool-control", "ToolHive Registry, Grafana"),
    "mcp-registry": ("tool-control", "MCP Registry"),
    "opensandbox-system": ("agent-runtime", "Headlamp, Grafana"),
    "codex-app-server": ("agent-runtime", "Codex console, Grafana"),
    "codex-platform": ("agent-runtime", "Codex console, Grafana"),
    "llm-gateway": ("model-control", "LiteLLM Admin, Grafana"),
    "ai-gateway": ("model-data", "Grafana"),
    "auth": ("identity-control", "Authentik, Headlamp"),
    "dex": ("identity-control", "Dex, Headlamp"),
    "harbor": ("artifact-control", "Harbor, Grafana"),
    "image": ("artifact-data", "Registry Image UI, Grafana"),
    "registry-image": ("artifact-data", "Harbor, Headlamp"),
    "pigsty-external": ("database-routing", "CloudBeaver, Pigsty, Grafana"),
    "supabase-pg-re8ch": ("database-platform", "Supabase Studio, CloudBeaver, Grafana"),
    "neo4j": ("database-platform", "Neo4j Browser, Grafana"),
    "fleet": ("device-control", "Fleet UI, Grafana"),
    "nuclio": ("application-control", "Nuclio Dashboard, Grafana"),
    "headlamp": ("human-control", "Headlamp"),
    "agent-collab": ("agent-data", "Headlamp, Grafana"),
    "anycam": ("application-data", "Headlamp, Grafana"),
    "anysite": ("application-data", "Headlamp, Grafana"),
    "bench": ("application-data", "Headlamp, Grafana"),
    "cardcat": ("application-data", "Headlamp, Grafana"),
    "comfy-agent": ("agent-data", "Headlamp, Grafana"),
    "compocv": ("application-data", "Headlamp, Grafana"),
    "coroot": ("observability-control", "Coroot, Grafana"),
    "ledger": ("application-data", "Headlamp, CloudBeaver, Neo4j Browser, Grafana"),
    "phonaid": ("application-data", "Headlamp, Grafana"),
    "qwen": ("application-data", "Nuclio Dashboard, Headlamp, Grafana"),
}

DOMAIN_NATIVE_AUTHORITY = {
    "cluster-control": "Kubernetes RBAC, admission policy and controller-specific authorization",
    "observability-control": "Grafana organizations/service accounts, product RBAC and Kubernetes RBAC",
    "observability-data": "tenant metrics/log proxies, OpenCost OAuth and product-native authorization",
    "platform-control": "Kubernetes RBAC and the managed service native administrator role",
    "tool-control": "ToolHive CR RBAC, tool allowlists, workload identity and Registry publication policy",
    "agent-runtime": "OpenSandbox/Codex runtime authorization and Kubernetes RBAC",
    "agent-data": "application identity, namespace RBAC and per-session/per-tenant data authorization",
    "model-control": "LiteLLM teams, virtual keys, budgets and administrator roles",
    "model-data": "gateway identity, model route policy and provider authorization",
    "identity-control": "Authentik/Dex roles, groups, policies and Kubernetes OIDC RBAC",
    "artifact-control": "Harbor project roles, robot scopes and Kubernetes image-pull authorization",
    "artifact-data": "Harbor project/repository roles and signed image publication policy",
    "database-routing": "PostgreSQL roles/database grants, PgBouncer policy and Kubernetes Secret RBAC",
    "database-platform": "database-native roles/RLS plus product-native authorization and Kubernetes RBAC",
    "device-control": "Fleet teams/roles, enrollment policy and Kubernetes RBAC",
    "application-control": "controller CR RBAC and application-native project/gateway authorization",
    "application-data": "namespace RBAC, application identity and backing-store native roles/RLS",
    "human-control": "human OIDC groups, Kubernetes RBAC and GUI-native roles",
}

SYSTEM_NAMESPACES = {"default", "kube-node-lease", "kube-public"}
MANAGED_PLATFORM_RESTARTS = {
    ("auth", "authentik-server"),
    ("auth", "authentik-worker"),
    ("llm-gateway", "litellm"),
    ("opensandbox-system", "opensandbox-server"),
    ("fleet", "fleet"),
    ("supabase-pg-re8ch", "supabase-studio"),
    ("nuclio", "nuclio-dashboard"),
    ("codex-platform", "codex-session-web"),
    ("observability-system", "re8ch-grafana"),
}

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

# Explicit cluster-control reads for the platform observe profile. These aliases
# avoid accepting an arbitrary namespace or API path from the caller while still
# exposing the Flux reconciliation state needed for GitHub-first operations.
PLATFORM_RESOURCE_PATHS = {
    "fluxdeployments": "/apis/apps/v1/namespaces/flux-system/deployments",
    "fluxgitrepositories": "/apis/source.toolkit.fluxcd.io/v1/namespaces/flux-system/gitrepositories",
    "fluxkustomizations": "/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-system/kustomizations",
    "fluxreceivers": "/apis/notification.toolkit.fluxcd.io/v1/namespaces/flux-system/receivers",
    "fluxproviders": "/apis/notification.toolkit.fluxcd.io/v1beta3/namespaces/flux-system/providers",
    "fluxalerts": "/apis/notification.toolkit.fluxcd.io/v1beta3/namespaces/flux-system/alerts",
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
    "fluxdeployments": "Deployment",
    "fluxgitrepositories": "GitRepository",
    "fluxkustomizations": "Kustomization",
    "fluxreceivers": "Receiver",
    "fluxproviders": "Provider",
    "fluxalerts": "Alert",
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
- GET  /api/grafana/render?preset=errors
- GET  /api/metrics/summary
- GET  /api/secrets/refs
- POST /api/secrets/database-credentials {"database":"qianwen_test"}
- GET  /api/secrets/web-logins
- POST /api/restart {"deployment":"name"}
- POST /api/k8s/proxy {"method":"GET","path":"/api/v1/namespaces/<ns>/pods","kubeToken":"..."}
- POST /api/postgres/query {"database":"qianwen_test","sql":"select now()","role":"app_ro"}
- GET  /api/admin/dex-users                    admin token required
- GET  /api/admin/dex-hash-instructions        admin token required
- POST /api/admin/generate-pg-tenant-database-plan admin token required
- POST /api/admin/ensure-tenant-access         admin token required
- POST /api/admin/create-dex-user              admin token required
- POST /api/admin/grant-user-namespace         admin token required
- POST /api/admin/ensure-harbor-tenant         admin token required
- POST /api/admin/ensure-tenant-database-secrets admin token required
- POST /api/admin/ensure-tenant-database-bundle  admin token required

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

Recommended PostgreSQL routing:
- Prefer tenant Secrets and the get_tenant_database_credentials tool instead of hardcoding database hosts.
- Direct write/admin/migration SQL: pgwrite.zt.re8ch.com:5433.
- Pooled app read/write direct PG: pgwrite.zt.re8ch.com:6432 when PgBouncer semantics fit the app.
- Direct normal reads: pgread-sh.zt.re8ch.com:5434 or pgread-gz.zt.re8ch.com:5434, chosen by region and health.
- CloudBeaver/DBeaver JDBC templates may use pgwrite.zt.re8ch.com:5432 for intentional instance-level GUI access, but do not treat 5432 as the HA write route.
- Cloud functions should prefer the PostgREST HA gateway on :3080 for simple CRUD: Shanghai write/strong read http://192.0.2.89:3080, Shanghai read http://192.0.2.112:3080, Guangzhou read http://192.0.2.251:3080.
- PostgreSQL 5432 is the instance port. It is useful for DBA inspection and some GUI clients, but the tenant default write route is 5433 or PgBouncer 6432.
- For Ops MCP postgres_sql_proxy specifically, the default route is instancePrimaryName / pgwrite.zt.re8ch.com:5432 because it is a network reachability helper for external agents and current tenant roles authenticate reliably on that direct instance entry. Use explicit route only when you intentionally need to test 5433/5434/6432.

Safety boundary:
- Tenant namespace: qwen
- Normal token writes are limited to restarting deployments in qwen.
- Node access is read-only for topology and metrics.
- Admin token can reconcile Dex static users and tenant RBAC.
- k8s_api_proxy only forwards Kubernetes API paths. The caller-provided kubeToken controls Kubernetes RBAC; Ops MCP only solves public-to-ZT/K3s API reachability.
- postgres_sql_proxy executes lightweight SQL from the Ops MCP network position. PostgreSQL username/password or tenant DB Secret role still controls database permissions; normal Ops tokens are limited to cataloged tenant databases.
"""

if OPS_TOOL_PROFILE != "legacy":
    GUIDE = GUIDE.replace("Ops MCP for re8ch/qwen", "Ops MCP for the RE8CH platform")
    GUIDE = GUIDE.replace(
        "- HTTP API and MCP both require Authorization: Bearer <ops-token>\n- You can also send X-Ops-Token: <ops-token>",
        "- Codex authenticates once with Dex OAuth at the ToolHive vMCP edge.\n- The internal Ops MCP backend accepts only its Kubernetes workload identity.",
    )
    GUIDE = GUIDE.replace(
        "- Tenant namespace: qwen\n- Normal token writes are limited to restarting deployments in qwen.",
        "- This platform profile is independent of every tenant, including qwen.\n- Write tools are restricted by the published allowlist and Dex groups.",
    )
    GUIDE = GUIDE.replace(
        "- Admin token can reconcile Dex static users and tenant RBAC.",
        "- The admin profile can reconcile Dex static users and tenant RBAC.",
    )

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


def parse_maybe_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def validate_k8s_proxy_path(path):
    if not path or not isinstance(path, str):
        raise ValueError("path is required")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise ValueError("path must be a Kubernetes API path, not a full URL")
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    allowed_exact = {"/api", "/apis", "/version", "/healthz", "/livez", "/readyz"}
    if path in allowed_exact or path.startswith("/api/") or path.startswith("/apis/"):
        return path
    raise ValueError("path is not an allowed Kubernetes API path")


def k8s_api_proxy(args):
    args = args or {}
    method = str(args.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("unsupported method: %s" % method)
    path = validate_k8s_proxy_path(args.get("path"))
    kube_token = args.get("kubeToken") or args.get("token")
    if not kube_token or not isinstance(kube_token, str):
        raise ValueError("kubeToken is required and must be provided by the caller")
    content_type = args.get("contentType") or args.get("content_type") or "application/json"
    body = args.get("body", None)
    data = None
    headers = {"Authorization": "Bearer " + kube_token}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            raise ValueError("body must be an object, array, string, or null")
        if len(data) > 1024 * 1024:
            raise ValueError("body too large; max 1 MiB")
        headers["Content-Type"] = content_type
    req = urllib.request.Request(KUBE_API + path, data=data, method=method, headers=headers)
    context = ssl.create_default_context(cafile=SERVICE_ACCOUNT_CA)
    try:
        with urllib.request.urlopen(req, timeout=int(args.get("timeoutSeconds") or 20), context=context) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {
                "kubernetesApi": KUBE_API,
                "method": method,
                "path": path,
                "status": resp.status,
                "body": parse_maybe_json(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "kubernetesApi": KUBE_API,
            "method": method,
            "path": path,
            "status": exc.code,
            "body": parse_maybe_json(raw),
        }


class PgProtocolError(RuntimeError):
    pass


def pg_read_message(sock):
    header = sock.recv(5)
    if len(header) != 5:
        raise PgProtocolError("short PostgreSQL message header")
    msg_type = header[:1]
    length = struct.unpack("!I", header[1:])[0]
    payload = b""
    remaining = length - 4
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise PgProtocolError("short PostgreSQL message body")
        payload += chunk
        remaining -= len(chunk)
    return msg_type, payload


def pg_send_message(sock, msg_type, payload=b""):
    sock.sendall(msg_type + struct.pack("!I", len(payload) + 4) + payload)


def pg_error_message(payload):
    parts = payload.split(b"\x00")
    fields = {}
    for part in parts:
        if len(part) >= 2:
            fields[part[:1].decode("ascii", errors="ignore")] = part[1:].decode("utf-8", errors="replace")
    return fields.get("M") or fields.get("S") or "PostgreSQL error"


def pg_cstring(value):
    return str(value).encode("utf-8") + b"\x00"


def parse_scram_fields(text):
    fields = {}
    for part in text.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def xor_bytes(left, right):
    return bytes(a ^ b for a, b in zip(left, right))


def pg_authenticate(sock, user, password, auth_payload):
    auth_code = struct.unpack("!I", auth_payload[:4])[0]
    if auth_code == 0:
        return
    if auth_code == 3:
        pg_send_message(sock, b"p", password.encode("utf-8") + b"\x00")
        return
    if auth_code == 5:
        salt = auth_payload[4:8]
        inner = hashlib.md5((password + user).encode("utf-8")).hexdigest().encode("ascii")
        outer = hashlib.md5(inner + salt).hexdigest()
        pg_send_message(sock, b"p", ("md5" + outer).encode("ascii") + b"\x00")
        return
    if auth_code == 10:
        mechanisms = [x.decode("ascii", errors="replace") for x in auth_payload[4:].split(b"\x00") if x]
        if "SCRAM-SHA-256" not in mechanisms:
            raise PgProtocolError("unsupported SASL mechanisms: %s" % ",".join(mechanisms))
        nonce = base64.b64encode(secrets.token_bytes(18)).decode("ascii").rstrip("=")
        client_first_bare = "n=%s,r=%s" % (user.replace("=", "=3D").replace(",", "=2C"), nonce)
        client_first = "n,," + client_first_bare
        first_payload = b"SCRAM-SHA-256\x00" + struct.pack("!I", len(client_first.encode("utf-8"))) + client_first.encode("utf-8")
        pg_send_message(sock, b"p", first_payload)
        msg_type, payload = pg_read_message(sock)
        if msg_type == b"E":
            raise PgProtocolError(pg_error_message(payload))
        if msg_type != b"R" or struct.unpack("!I", payload[:4])[0] != 11:
            raise PgProtocolError("expected SASLContinue")
        server_first = payload[4:].decode("utf-8", errors="replace")
        fields = parse_scram_fields(server_first)
        server_nonce = fields.get("r", "")
        if not server_nonce.startswith(nonce):
            raise PgProtocolError("invalid SCRAM server nonce")
        salt = base64.b64decode(fields.get("s", ""))
        iterations = int(fields.get("i", "0"))
        if iterations <= 0:
            raise PgProtocolError("invalid SCRAM iteration count")
        client_final_without_proof = "c=biws,r=%s" % server_nonce
        auth_message = "%s,%s,%s" % (client_first_bare, server_first, client_final_without_proof)
        salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        signature = hmac.new(stored_key, auth_message.encode("utf-8"), hashlib.sha256).digest()
        proof = base64.b64encode(xor_bytes(client_key, signature)).decode("ascii")
        client_final = "%s,p=%s" % (client_final_without_proof, proof)
        pg_send_message(sock, b"p", client_final.encode("utf-8"))
        msg_type, payload = pg_read_message(sock)
        if msg_type == b"E":
            raise PgProtocolError(pg_error_message(payload))
        if msg_type != b"R" or struct.unpack("!I", payload[:4])[0] != 12:
            raise PgProtocolError("expected SASLFinal")
        return
    raise PgProtocolError("unsupported PostgreSQL authentication code: %s" % auth_code)


def pg_connect(host, port, database, user, password, timeout=10):
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    sock.settimeout(timeout)
    params = {
        "user": user,
        "database": database,
        "application_name": "ops-mcp-sql-proxy",
        "client_encoding": "UTF8",
    }
    payload = struct.pack("!I", 196608)
    for key, value in params.items():
        payload += pg_cstring(key) + pg_cstring(value)
    payload += b"\x00"
    sock.sendall(struct.pack("!I", len(payload) + 4) + payload)
    while True:
        msg_type, msg_payload = pg_read_message(sock)
        if msg_type == b"R":
            pg_authenticate(sock, user, password, msg_payload)
        elif msg_type == b"E":
            raise PgProtocolError(pg_error_message(msg_payload))
        elif msg_type == b"Z":
            return sock
        elif msg_type in (b"S", b"K", b"N"):
            continue
        else:
            continue


def pg_execute_simple(sock, sql, max_rows=100):
    pg_send_message(sock, b"Q", sql.encode("utf-8") + b"\x00")
    columns = []
    rows = []
    command = ""
    notices = []
    truncated = False
    while True:
        msg_type, payload = pg_read_message(sock)
        if msg_type == b"T":
            count = struct.unpack("!H", payload[:2])[0]
            offset = 2
            columns = []
            for _ in range(count):
                end = payload.index(b"\x00", offset)
                name = payload[offset:end].decode("utf-8", errors="replace")
                offset = end + 1 + 18
                columns.append(name)
        elif msg_type == b"D":
            count = struct.unpack("!H", payload[:2])[0]
            offset = 2
            row = []
            for _ in range(count):
                length = struct.unpack("!i", payload[offset:offset + 4])[0]
                offset += 4
                if length == -1:
                    row.append(None)
                else:
                    value = payload[offset:offset + length].decode("utf-8", errors="replace")
                    offset += length
                    row.append(value)
            if len(rows) < max_rows:
                rows.append(row)
            else:
                truncated = True
        elif msg_type == b"C":
            command = payload.rstrip(b"\x00").decode("utf-8", errors="replace")
        elif msg_type == b"N":
            notices.append(pg_error_message(payload))
        elif msg_type == b"E":
            raise PgProtocolError(pg_error_message(payload))
        elif msg_type == b"Z":
            return {"columns": columns, "rows": rows, "rowCount": len(rows), "truncated": truncated, "command": command, "notices": notices}


READONLY_SQL_RE = re.compile(r"^\s*(select|with|show|explain|values)\b", re.IGNORECASE | re.DOTALL)


def normalize_sql_for_safety(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql is required")
    if len(sql.encode("utf-8")) > 20000:
        raise ValueError("sql too large; max 20 KB")
    stripped = sql.strip()
    if ";" in stripped[:-1]:
        raise ValueError("multiple SQL statements are not allowed")
    return stripped[:-1].strip() if stripped.endswith(";") else stripped


def tenant_db_secret_role(secret_data, database, role):
    prefix = database_env_prefix(database)
    role_key = "".join(ch.upper() if ch.isalnum() else "_" for ch in role)
    user = secret_data.get("%s_%s_USER" % (prefix, role_key))
    password = secret_data.get("%s_%s_PASSWORD" % (prefix, role_key))
    if user and password:
        return user, password
    if role == "app_ro":
        return secret_data.get("%s_APP_RO_USER" % prefix), secret_data.get("%s_APP_RO_PASSWORD" % prefix)
    if role == "app_rw":
        return secret_data.get("%s_APP_RW_USER" % prefix), secret_data.get("%s_APP_RW_PASSWORD" % prefix)
    if role == "migrator":
        return secret_data.get("%s_MIGRATOR_USER" % prefix), secret_data.get("%s_MIGRATOR_PASSWORD" % prefix)
    if role == "pgrst":
        return secret_data.get("%s_PGRST_USER" % prefix), secret_data.get("%s_PGRST_PASSWORD" % prefix)
    if role == "admin":
        return secret_data.get("%s_ADMIN_USER" % prefix), secret_data.get("%s_ADMIN_PASSWORD" % prefix)
    raise ValueError("unsupported database role: %s" % role)


def pg_route_from_args(args, read_only=True):
    route = args.get("route") or "instancePrimaryName"
    if route not in PG_SERVICE_ROUTES or "host" not in PG_SERVICE_ROUTES[route]:
        raise ValueError("route must be one of: %s" % ", ".join(sorted(k for k, v in PG_SERVICE_ROUTES.items() if "host" in v)))
    return PG_SERVICE_ROUTES[route]["host"], int(PG_SERVICE_ROUTES[route]["port"]), route


def postgres_sql_proxy(args=None, admin=False):
    args = args or {}
    namespace = namespace_for_secret_args(args, admin=admin)
    database = args.get("database") or args.get("db") or ""
    pg_ident(database)
    allowed = allowed_databases_for_namespace(namespace)
    if not admin and database not in allowed:
        raise PermissionError("database is not in tenant catalog: %s" % database)
    read_only = bool(args.get("readOnly", True))
    sql = normalize_sql_for_safety(args.get("sql"))
    if read_only and not READONLY_SQL_RE.match(sql):
        raise PermissionError("readOnly SQL proxy only allows SELECT/WITH/SHOW/EXPLAIN/VALUES")
    max_rows = min(max(int(args.get("maxRows") or 100), 1), 500)
    timeout = min(max(int(args.get("timeoutSeconds") or 10), 1), 30)
    role = args.get("role") or ("app_ro" if read_only else "app_rw")
    user = args.get("user") or args.get("username")
    password = args.get("password")
    secret_ref = None
    if not (user and password):
        creds = get_tenant_database_credentials({"namespace": namespace, "database": database}, admin=admin)
        if creds.get("status") != "complete":
            raise RuntimeError("database credential secret is not complete: %s" % creds)
        user, password = tenant_db_secret_role(creds.get("data", {}), database, role)
        secret_ref = creds.get("secret")
    if not user or not password:
        raise ValueError("database user/password not available for role: %s" % role)
    host, port, route = pg_route_from_args(args, read_only=read_only)
    sock = None
    try:
        sock = pg_connect(host, port, database, user, password, timeout=timeout)
        pg_execute_simple(sock, "SET statement_timeout = %s" % sql_literal("%sms" % (timeout * 1000)), max_rows=1)
        if read_only:
            pg_execute_simple(sock, "SET default_transaction_read_only = on", max_rows=1)
        result = pg_execute_simple(sock, sql, max_rows=max_rows)
        result.update({
            "tenant": current_tenant(),
            "namespace": namespace,
            "database": database,
            "route": route,
            "host": host,
            "port": port,
            "user": user,
            "role": role,
            "readOnly": read_only,
            "credentialSecret": secret_ref,
            "note": "Ops MCP only proxies network reachability. PostgreSQL authentication and role permissions are still enforced by the supplied database credentials.",
        })
        return result
    finally:
        if sock is not None:
            try:
                pg_send_message(sock, b"X", b"")
                sock.close()
            except Exception:
                pass

def resource_path(kind):
    if kind == "nodes":
        return "/api/v1/nodes"
    if kind == "nodemetrics":
        return "/apis/metrics.k8s.io/v1beta1/nodes"
    if kind in PLATFORM_RESOURCE_PATHS:
        if OPS_TOOL_PROFILE not in ("observe", "admin"):
            raise PermissionError("Flux control-plane reads require the platform observe profile")
        return PLATFORM_RESOURCE_PATHS[kind]
    if kind not in RESOURCE_PATHS:
        raise ValueError("unsupported kind: %s" % kind)
    return RESOURCE_PATHS[kind] % current_namespace()

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
    elif kind == "NodeMetrics":
        # Metrics API objects contain only an aggregate CPU/memory sample.
        # Preserve it for capacity diagnostics while withholding unrelated
        # Kubernetes object data from the public observe profile.
        out["timestamp"] = item.get("timestamp")
        out["window"] = item.get("window")
        out["usage"] = {key: item.get("usage", {}).get(key) for key in ("cpu", "memory")}
    elif kind == "PodMetrics":
        out["timestamp"] = item.get("timestamp")
        out["window"] = item.get("window")
        out["containers"] = [
            {"name": container.get("name"), "usage": {key: container.get("usage", {}).get(key) for key in ("cpu", "memory")}}
            for container in item.get("containers", [])
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
    elif kind in ("GitRepository", "Kustomization", "Receiver", "Provider", "Alert"):
        out["suspended"] = bool(spec.get("suspend", False))
        out["conditions"] = [
            {key: condition.get(key) for key in ("type", "status", "reason", "message")}
            for condition in status.get("conditions", [])
        ]
        if kind == "GitRepository":
            out["url"] = spec.get("url")
            out["revision"] = status.get("artifact", {}).get("revision")
        elif kind == "Kustomization":
            out["lastAppliedRevision"] = status.get("lastAppliedRevision")
            out["sourceRef"] = spec.get("sourceRef")
        elif kind == "Receiver":
            out["webhookPath"] = status.get("webhookPath")
            out["receiverType"] = spec.get("type")
        elif kind == "Provider":
            out["providerType"] = spec.get("type")
            out["address"] = spec.get("address")
        elif kind == "Alert":
            out["providerRef"] = spec.get("providerRef")
    else:
        out["status"] = status if status else None
    return out

def list_resources(kind):
    doc = kube_request("GET", resource_path(kind))
    items = doc.get("items", [])
    default_kind = RESOURCE_KINDS.get(kind)
    return {"kind": kind, "tenant": current_tenant(), "namespace": current_namespace(), "count": len(items), "items": [simplify_resource(x, default_kind) for x in items]}

def get_secret_token(secret_name):
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (current_namespace(), secret_name))
    encoded = doc.get("data", {}).get("token")
    if not encoded:
        raise RuntimeError("secret %s has no data.token" % secret_name)
    return base64.b64decode(encoded).decode("utf-8")

def get_secret_data(secret_name):
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (current_namespace(), secret_name))
    data = {}
    for key, value in doc.get("data", {}).items():
        data[key] = base64.b64decode(value).decode("utf-8")
    return data

ALLOWED_TENANT_SECRET_PURPOSES = {
    "postgres-credentials",
    "headlamp-login",
    "cloudbeaver-login",
    "grafana-web-login",
    "grafana-token",
    "harbor-project-admin",
    "harbor-pull",
    "harbor-web-login",
    "kubectl-operator-token",
    "kubectl-viewer-token",
    "kubectl-user-token",
}


def secret_name_for_database(database):
    return database.replace("_", "-") + "-db-credentials"


def database_env_prefix(database):
    return "".join(ch.upper() if ch.isalnum() else "_" for ch in database)


def decode_secret_data(doc):
    out = {}
    for key, value in doc.get("data", {}).items():
        out[key] = base64.b64decode(value).decode("utf-8")
    return out


def secret_summary(doc):
    meta = doc.get("metadata", {})
    labels = meta.get("labels", {})
    annotations = meta.get("annotations", {})
    return {
        "namespace": meta.get("namespace"),
        "name": meta.get("name"),
        "type": doc.get("type", "Opaque"),
        "purpose": labels.get("re8ch.com/secret-purpose", ""),
        "tenant": labels.get("re8ch.com/tenant", ""),
        "database": labels.get("re8ch.com/database", ""),
        "credentialStatus": annotations.get("re8ch.com/credential-status", ""),
        "keys": sorted((doc.get("data") or {}).keys()),
    }


def tenant_catalog_for_namespace(namespace):
    for key, value in TENANT_RESOURCE_CATALOG.items():
        if value.get("kubernetes", {}).get("namespace") == namespace:
            return key, value
    return namespace, TENANT_RESOURCE_CATALOG.get(namespace, {})


def namespace_for_secret_args(args, admin=False):
    namespace = args.get("namespace") or current_namespace()
    validate_k8s_name(namespace, "namespace")
    if not admin and namespace != current_namespace():
        raise PermissionError("normal token can only read tenant namespace: %s" % current_namespace())
    return namespace


def list_allowed_secret_docs(namespace):
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets" % namespace)
    out = []
    for item in doc.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        if labels.get("re8ch.com/tenant") != namespace:
            continue
        if labels.get("re8ch.com/secret-purpose") not in ALLOWED_TENANT_SECRET_PURPOSES:
            continue
        out.append(item)
    return out


def get_tenant_secret_refs(args=None, admin=False):
    args = args or {}
    if admin and bool(args.get("allTenants", False)):
        tenants = sorted({v.get("kubernetes", {}).get("namespace") for v in TENANT_RESOURCE_CATALOG.values() if v.get("kubernetes", {}).get("namespace")})
    else:
        tenants = [namespace_for_secret_args(args, admin=admin)]
    result = {}
    for namespace in tenants:
        result[namespace] = [secret_summary(item) for item in list_allowed_secret_docs(namespace)]
    return {"tenant": current_tenant(), "namespace": current_namespace(), "secrets": result}


def allowed_databases_for_namespace(namespace):
    _, catalog = tenant_catalog_for_namespace(namespace)
    return catalog.get("pigsty", {}).get("databases", [])


def get_database_secret_reference(namespace, database):
    _, catalog = tenant_catalog_for_namespace(namespace)
    refs = catalog.get("pigsty", {}).get("credentialSecrets", {})
    if database in refs:
        return refs[database]
    return {"namespace": namespace, "name": secret_name_for_database(database), "status": "unknown"}


def read_allowed_tenant_secret(namespace, name, expected_purpose=None):
    name = validate_k8s_name(name, "secret")
    doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (namespace, name))
    labels = doc.get("metadata", {}).get("labels", {})
    purpose = labels.get("re8ch.com/secret-purpose", "")
    if labels.get("re8ch.com/tenant") != namespace or purpose not in ALLOWED_TENANT_SECRET_PURPOSES:
        raise PermissionError("secret is not a tenant-readable credential: %s/%s" % (namespace, name))
    if expected_purpose and purpose != expected_purpose:
        raise PermissionError("secret purpose mismatch for %s/%s" % (namespace, name))
    return doc


def get_tenant_database_credentials(args=None, admin=False):
    args = args or {}
    namespace = namespace_for_secret_args(args, admin=admin)
    database = args.get("database") or args.get("db") or ""
    if not database:
        raise ValueError("database is required")
    pg_ident(database)
    allowed = allowed_databases_for_namespace(namespace)
    if not admin and database not in allowed:
        raise PermissionError("database is not in tenant catalog: %s" % database)
    ref = get_database_secret_reference(namespace, database)
    ref_namespace = ref.get("namespace", namespace)
    if not admin and ref_namespace != namespace:
        raise PermissionError("normal token cannot read cross-namespace database secret")
    name = ref.get("name") or secret_name_for_database(database)
    try:
        doc = read_allowed_tenant_secret(ref_namespace, name, expected_purpose="postgres-credentials")
    except KubeError as exc:
        if exc.status == 404:
            return {"database": database, "secret": "%s/%s" % (ref_namespace, name), "status": ref.get("status", "missing"), "note": ref.get("note", "credential secret not found")}
        raise
    return {
        "database": database,
        "secret": "%s/%s" % (ref_namespace, name),
        "status": doc.get("metadata", {}).get("annotations", {}).get("re8ch.com/credential-status", "complete"),
        "recommendedRoutes": PG_SERVICE_ROUTES,
        "usageNotes": [
            "Prefer DATABASE_URL_RW from this Secret for direct writes/admin SQL; new Secrets use pgwrite.zt.re8ch.com:5433.",
            "Prefer DATABASE_URL_POOL_RW for high-churn app clients when PgBouncer transaction/session semantics are acceptable.",
            "Prefer DATABASE_URL_RO for normal reads; choose the regional read service by health.",
            "For simple HTTP CRUD from Nuclio/cloud functions, prefer the PostgREST :3080 gateways instead of direct PostgreSQL.",
            "CloudBeaver/DBeaver may use 5432 JDBC templates for GUI inspection, but 5432 is a PostgreSQL instance port, not the recommended HA write route.",
        ],
        "metadata": secret_summary(doc),
        "data": decode_secret_data(doc),
    }


def decode_dockerconfig_secret(doc):
    encoded = (doc.get("data") or {}).get(".dockerconfigjson")
    if not encoded:
        return None
    cfg = json.loads(base64.b64decode(encoded).decode("utf-8"))
    entries = []
    for registry, auth in cfg.get("auths", {}).items():
        entries.append({
            "registry": registry,
            "username": auth.get("username", ""),
            "password": auth.get("password", ""),
        })
    return entries


def get_tenant_web_logins(args=None, admin=False):
    args = args or {}
    namespace = namespace_for_secret_args(args, admin=admin)
    docs = list_allowed_secret_docs(namespace)
    result = {"namespace": namespace, "headlamp": None, "cloudbeaver": None, "grafana": [], "harbor": []}
    for doc in docs:
        labels = doc.get("metadata", {}).get("labels", {})
        purpose = labels.get("re8ch.com/secret-purpose")
        name = doc.get("metadata", {}).get("name")
        if purpose == "headlamp-login":
            result["headlamp"] = {"secret": "%s/%s" % (namespace, name), **decode_secret_data(doc)}
        elif purpose == "cloudbeaver-login":
            result["cloudbeaver"] = {"secret": "%s/%s" % (namespace, name), **decode_secret_data(doc)}
        elif purpose in ("grafana-token", "grafana-web-login"):
            data = decode_secret_data(doc)
            result["grafana"].append({"secret": "%s/%s" % (namespace, name), "purpose": purpose, **data})
        elif purpose in ("harbor-project-admin", "harbor-pull", "harbor-web-login"):
            decoded = decode_dockerconfig_secret(doc)
            if decoded is not None:
                for item in decoded:
                    result["harbor"].append({"secret": "%s/%s" % (namespace, name), "purpose": purpose, **item})
            else:
                data = decode_secret_data(doc)
                result["harbor"].append({"secret": "%s/%s" % (namespace, name), "purpose": purpose, **data})
    return result

def build_kubeconfig():
    token = get_secret_token(current_kube_token_secret())
    ca_data = base64.b64encode(open(SERVICE_ACCOUNT_CA, "rb").read()).decode("ascii")
    namespace = current_namespace()
    user = "%s-operator" % namespace
    return """apiVersion: v1
kind: Config
clusters:
- name: re8ch-k3s
  cluster:
    server: %s
    certificate-authority-data: %s
contexts:
- name: %s@re8ch-k3s
  context:
    cluster: re8ch-k3s
    namespace: %s
    user: %s
current-context: %s@re8ch-k3s
users:
- name: %s
  user:
    token: %s
""" % (KUBE_SERVER_PUBLIC, ca_data, namespace, namespace, user, namespace, user, token)

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
        "/apis/apps/v1/namespaces/%s/deployments/%s" % (current_namespace(), urllib.parse.quote(name)),
        patch,
        "application/strategic-merge-patch+json",
    )
    return {"tenant": current_tenant(), "namespace": current_namespace(), "deployment": name, "generation": doc.get("metadata", {}).get("generation")}

def restart_managed_platform_deployment(args):
    args = args or {}
    namespace = str(args.get("namespace") or "").strip()
    deployment = str(args.get("deployment") or "").strip()
    if (namespace, deployment) not in MANAGED_PLATFORM_RESTARTS:
        raise ValueError("deployment is not in the reviewed platform restart allowlist")
    restarted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    patch = {"spec": {"template": {"metadata": {"annotations": {
        "ops.re8ch.com/restartedAt": restarted_at,
        "ops.re8ch.com/restartedBy": "toolhive-platform-operate",
    }}}}}
    doc = kube_request(
        "PATCH",
        "/apis/apps/v1/namespaces/%s/deployments/%s" % (
            urllib.parse.quote(namespace), urllib.parse.quote(deployment)
        ),
        patch,
        "application/strategic-merge-patch+json",
    )
    return {
        "namespace": namespace,
        "deployment": deployment,
        "restartedAt": restarted_at,
        "generation": doc.get("metadata", {}).get("generation"),
        "humanTracking": {"headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def scale_managed_platform_deployment(args):
    args = args or {}
    namespace = str(args.get("namespace") or "").strip()
    deployment = str(args.get("deployment") or "").strip()
    replicas = args.get("replicas")
    if (namespace, deployment) not in MANAGED_PLATFORM_RESTARTS:
        raise ValueError("deployment is not in the reviewed platform scale allowlist")
    if replicas not in (0, 1):
        raise ValueError("replicas must be exactly 0 or 1")
    changed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = "/apis/apps/v1/namespaces/%s/deployments/%s" % (
        urllib.parse.quote(namespace), urllib.parse.quote(deployment)
    )
    before = kube_request("GET", path)
    previous = before.get("spec", {}).get("replicas", 0)
    patch = {
        "metadata": {"annotations": {
            "ops.re8ch.com/scaledAt": changed_at,
            "ops.re8ch.com/scaledBy": "toolhive-platform-operate",
        }},
        "spec": {"replicas": replicas},
    }
    doc = kube_request("PATCH", path, patch, "application/strategic-merge-patch+json")
    return {
        "namespace": namespace,
        "deployment": deployment,
        "previousReplicas": previous,
        "requestedReplicas": replicas,
        "changedAt": changed_at,
        "generation": doc.get("metadata", {}).get("generation"),
        "humanTracking": {"headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_managed_platform_operation_status():
    results = []
    for namespace, deployment in sorted(MANAGED_PLATFORM_RESTARTS):
        path = "/apis/apps/v1/namespaces/%s/deployments/%s" % (
            urllib.parse.quote(namespace), urllib.parse.quote(deployment)
        )
        try:
            doc = kube_request("GET", path)
            meta, spec, status = doc.get("metadata", {}), doc.get("spec", {}), doc.get("status", {})
            annotations = meta.get("annotations", {})
            template_annotations = spec.get("template", {}).get("metadata", {}).get("annotations", {})
            results.append({
                "namespace": namespace,
                "deployment": deployment,
                "desired": spec.get("replicas", 0),
                "ready": status.get("readyReplicas", 0),
                "available": status.get("availableReplicas", 0),
                "generation": meta.get("generation"),
                "observedGeneration": status.get("observedGeneration"),
                "lastScale": {
                    "at": annotations.get("ops.re8ch.com/scaledAt"),
                    "by": annotations.get("ops.re8ch.com/scaledBy"),
                },
                "lastRestart": {
                    "at": template_annotations.get("ops.re8ch.com/restartedAt"),
                    "by": template_annotations.get("ops.re8ch.com/restartedBy"),
                },
            })
        except KubeError as exc:
            results.append({"namespace": namespace, "deployment": deployment, "error": str(exc), "httpStatus": exc.status})
    return {
        "targets": results,
        "summary": {
            "total": len(results),
            "available": sum(item.get("available", 0) > 0 for item in results),
            "scaledToZero": sum(item.get("desired") == 0 for item in results),
            "withRecordedOperation": sum(bool(item.get("lastScale", {}).get("at") or item.get("lastRestart", {}).get("at")) for item in results),
        },
        "humanTracking": {"headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_nuclio_function_status():
    docs = kube_request("GET", "/apis/nuclio.io/v1beta1/nucliofunctions").get("items", [])
    functions = []
    for doc in docs:
        meta, spec, status = doc.get("metadata", {}), doc.get("spec", {}), doc.get("status", {})
        functions.append({
            "namespace": meta.get("namespace"),
            "name": meta.get("name"),
            "generation": meta.get("generation"),
            "state": status.get("state") or "unknown",
            "message": str(status.get("message") or "")[:240],
            "image": spec.get("image"),
            "minReplicas": spec.get("minReplicas"),
            "maxReplicas": spec.get("maxReplicas"),
            "nodeSelector": spec.get("nodeSelector", {}),
            "internalInvocationUrls": status.get("internalInvocationUrls", []),
            "containerImage": status.get("containerImage"),
        })
    functions.sort(key=lambda item: (item["namespace"] or "", item["name"] or ""))
    states = {}
    for item in functions:
        states[item["state"]] = states.get(item["state"], 0) + 1
    return {
        "summary": {"total": len(functions), "states": states},
        "functions": functions,
        "redaction": "Environment variables, Secret references, annotations and last-applied configuration are intentionally omitted.",
        "humanTracking": {
            "nuclioDashboard": "http://nuclio-dashboard.nuclio.svc.cluster.local:8070",
            "headlamp": HEADLAMP_PUBLIC,
            "grafana": GRAFANA_PUBLIC,
        },
    }

def diagnose_nuclio_function(args):
    args = args or {}
    namespace = validate_k8s_name(str(args.get("namespace") or ""), "namespace")
    name = validate_k8s_name(str(args.get("name") or ""), "name")
    function_path = "/apis/nuclio.io/v1beta1/namespaces/%s/nucliofunctions/%s" % (
        urllib.parse.quote(namespace), urllib.parse.quote(name)
    )
    function = kube_request("GET", function_path)
    spec, function_status = function.get("spec", {}), function.get("status", {})
    pods = kube_request("GET", "/api/v1/namespaces/%s/pods" % urllib.parse.quote(namespace)).get("items", [])
    deployments = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % urllib.parse.quote(namespace)).get("items", [])
    events = kube_request("GET", "/api/v1/namespaces/%s/events" % urllib.parse.quote(namespace)).get("items", [])

    def belongs(meta):
        labels = meta.get("labels", {})
        owner_names = {owner.get("name", "") for owner in meta.get("ownerReferences", [])}
        return (
            labels.get("nuclio.io/name") == name or
            labels.get("nuclio.io/function-name") == name or
            labels.get("nuclio.io/function-name") == "nuclio-%s" % name or
            name in owner_names or "nuclio-%s" % name in owner_names or
            meta.get("name", "").startswith("nuclio-%s-" % name)
        )

    matching_pods = [pod for pod in pods if belongs(pod.get("metadata", {}))]
    matching_deployments = [deployment for deployment in deployments if belongs(deployment.get("metadata", {}))]
    involved_names = {name, "nuclio-%s" % name}
    involved_names.update(item.get("metadata", {}).get("name") for item in matching_pods + matching_deployments)
    event_rows = []
    for event in events:
        involved = event.get("involvedObject", {})
        if involved.get("name") not in involved_names:
            continue
        event_rows.append({
            "type": event.get("type"),
            "reason": event.get("reason"),
            "message": str(event.get("message") or "")[:400],
            "count": event.get("count", 1),
            "lastTimestamp": event.get("lastTimestamp") or event.get("eventTime"),
            "object": "%s/%s" % (involved.get("kind", ""), involved.get("name", "")),
        })
    event_rows = event_rows[-20:]
    pod_rows = []
    for pod in matching_pods:
        meta, status = pod.get("metadata", {}), pod.get("status", {})
        waiting = []
        for container in status.get("containerStatuses", []):
            state = container.get("state", {}).get("waiting")
            if state:
                waiting.append({"container": container.get("name"), "reason": state.get("reason"), "message": str(state.get("message") or "")[:300]})
        scheduled = next((condition for condition in status.get("conditions", []) if condition.get("type") == "PodScheduled"), {})
        pod_rows.append({
            "name": meta.get("name"),
            "phase": status.get("phase"),
            "node": spec_node if (spec_node := pod.get("spec", {}).get("nodeName")) else None,
            "scheduled": scheduled.get("status"),
            "scheduleReason": scheduled.get("reason"),
            "scheduleMessage": str(scheduled.get("message") or "")[:400],
            "waiting": waiting,
        })
    deployment_rows = []
    for deployment in matching_deployments:
        meta, deployment_spec, status = deployment.get("metadata", {}), deployment.get("spec", {}), deployment.get("status", {})
        deployment_rows.append({
            "name": meta.get("name"),
            "desired": deployment_spec.get("replicas", 0),
            "ready": status.get("readyReplicas", 0),
            "available": status.get("availableReplicas", 0),
            "conditions": [{"type": item.get("type"), "status": item.get("status"), "reason": item.get("reason"), "message": str(item.get("message") or "")[:300]} for item in status.get("conditions", [])],
        })
    return {
        "namespace": namespace,
        "name": name,
        "function": {
            "state": function_status.get("state") or "unknown",
            "message": str(function_status.get("message") or "")[:500],
            "image": spec.get("image"),
            "minReplicas": spec.get("minReplicas"),
            "maxReplicas": spec.get("maxReplicas"),
            "nodeSelector": spec.get("nodeSelector", {}),
        },
        "deployments": deployment_rows,
        "pods": pod_rows,
        "recentEvents": event_rows,
        "redaction": "Environment, Secret and annotation data are omitted.",
        "humanTracking": {"nuclioDashboard": "http://nuclio-dashboard.nuclio.svc.cluster.local:8070", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_nuclio_capacity_status():
    nodes = kube_request("GET", "/api/v1/nodes").get("items", [])
    pods = kube_request("GET", "/api/v1/pods").get("items", [])

    def is_nuclio_pod(pod):
        meta = pod.get("metadata", {})
        labels = meta.get("labels", {})
        return (
            meta.get("name", "").startswith("nuclio-") or
            labels.get("nuclio.io/class") == "function" or
            "nuclio.io/function-name" in labels
        )

    nuclio_pods = [pod for pod in pods if is_nuclio_pod(pod)]
    rows = []
    for node in nodes:
        meta, spec, status = node.get("metadata", {}), node.get("spec", {}), node.get("status", {})
        if meta.get("labels", {}).get("pigsty.cc/workload") != "nuclio":
            continue
        name = meta.get("name")
        ready_condition = next((item for item in status.get("conditions", []) if item.get("type") == "Ready"), {})
        node_pods = [pod for pod in nuclio_pods if pod.get("spec", {}).get("nodeName") == name]
        rows.append({
            "node": name,
            "ready": ready_condition.get("status", "Unknown"),
            "readyReason": ready_condition.get("reason"),
            "readyMessage": str(ready_condition.get("message") or "")[:300],
            "unschedulable": bool(spec.get("unschedulable", False)),
            "taints": [{"key": item.get("key"), "value": item.get("value"), "effect": item.get("effect")} for item in spec.get("taints", [])],
            "allocatable": {key: status.get("allocatable", {}).get(key) for key in ("cpu", "memory", "pods")},
            "nuclioPods": {
                "total": len(node_pods),
                "running": sum(pod.get("status", {}).get("phase") == "Running" for pod in node_pods),
                "ready": sum(any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in pod.get("status", {}).get("conditions", [])) for pod in node_pods),
                "terminating": sum(bool(pod.get("metadata", {}).get("deletionTimestamp")) for pod in node_pods),
            },
        })
    rows.sort(key=lambda item: item["node"] or "")
    pending = []
    for pod in nuclio_pods:
        if pod.get("spec", {}).get("nodeName"):
            continue
        meta, status = pod.get("metadata", {}), pod.get("status", {})
        scheduled = next((item for item in status.get("conditions", []) if item.get("type") == "PodScheduled"), {})
        pending.append({
            "namespace": meta.get("namespace"),
            "pod": meta.get("name"),
            "reason": scheduled.get("reason"),
            "message": str(scheduled.get("message") or "")[:400],
        })
    return {
        "summary": {
            "nodes": len(rows),
            "readySchedulableNodes": sum(item["ready"] == "True" and not item["unschedulable"] for item in rows),
            "nuclioPods": len(nuclio_pods),
            "pendingPods": len(pending),
        },
        "nodes": rows,
        "pendingPods": pending[:50],
        "rolloutGate": "Do not start a rollout unless at least one matching node is Ready, schedulable, tolerates the workload, and has sufficient allocatable capacity.",
        "humanTracking": {"headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_litellm_gateway_status():
    base = "http://litellm.llm-gateway.svc.cluster.local:4000"
    checks = []
    readiness = None
    for name, path in (("liveness", "/health/liveliness"), ("readiness", "/health/readiness")):
        started = time.monotonic()
        try:
            request = urllib.request.Request(base + path, headers={"User-Agent": "re8ch-ops-mcp-health/1.0"})
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read(4096).decode("utf-8", "replace")
                status_code = response.status
            parsed = parse_maybe_json(raw)
            if name == "readiness" and isinstance(parsed, dict):
                readiness = {"status": parsed.get("status"), "db": parsed.get("db")}
            checks.append({"name": name, "status": "healthy" if status_code == 200 else "degraded", "httpStatus": status_code, "latencyMs": int((time.monotonic() - started) * 1000)})
        except Exception as exc:
            checks.append({"name": name, "status": "unreachable", "httpStatus": None, "latencyMs": int((time.monotonic() - started) * 1000), "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
    deployment = kube_request("GET", "/apis/apps/v1/namespaces/llm-gateway/deployments/litellm")
    meta, spec, status = deployment.get("metadata", {}), deployment.get("spec", {}), deployment.get("status", {})
    return {
        "checks": checks,
        "readiness": readiness,
        "deployment": {
            "generation": meta.get("generation"),
            "observedGeneration": status.get("observedGeneration"),
            "desired": spec.get("replicas", 0),
            "ready": status.get("readyReplicas", 0),
            "available": status.get("availableReplicas", 0),
        },
        "protectedNativeAPIs": ["/health", "/health/services", "/model/info", "/v1/models", "/key/info", "/team/list"],
        "authorization": "Protected model, provider, key, team and budget APIs require LiteLLM native credentials and are not proxied by this tool.",
        "redaction": "Master key, provider keys, database URL, UI credentials and ConfigMap contents are not read.",
        "humanTracking": {"litellmAdmin": "https://litellm.re8ch.com", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_fleet_control_status():
    base = "http://fleet.fleet.svc.cluster.local:8080"
    health = probe_http("fleet-health", "device-control", base + "/healthz", "Fleet public liveness")
    version = None
    version_error = None
    try:
        request = urllib.request.Request(base + "/version", headers={"User-Agent": "re8ch-ops-mcp-health/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            parsed = parse_maybe_json(response.read(4096).decode("utf-8", "replace"))
        if isinstance(parsed, dict):
            version = {key: parsed.get(key) for key in ("version", "branch", "revision", "go_version", "build_date")}
    except Exception as exc:
        version_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    deployments = kube_request("GET", "/apis/apps/v1/namespaces/fleet/deployments").get("items", [])
    statefulsets = kube_request("GET", "/apis/apps/v1/namespaces/fleet/statefulsets").get("items", [])
    workloads = []
    for items, kind in ((deployments, "Deployment"), (statefulsets, "StatefulSet")):
        for item in items:
            meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
            workloads.append({
                "kind": kind,
                "name": meta.get("name"),
                "desired": spec.get("replicas", 0),
                "ready": status.get("readyReplicas", 0),
                "available": status.get("availableReplicas", status.get("currentReplicas", 0)),
            })
    workloads.sort(key=lambda item: (item["kind"] or "", item["name"] or ""))
    return {
        "health": health,
        "version": version,
        "versionError": version_error,
        "workloads": workloads,
        "protectedNativeAPIs": ["/api/v1/fleet/config", "/api/v1/fleet/hosts", "/api/v1/fleet/queries", "/api/v1/fleet/activities"],
        "authorization": "Host inventory, queries, policies, activities and campaigns require Fleet native authorization and are not proxied.",
        "redaction": "Fleet API tokens, device data, policies, query results and MySQL/Redis credentials are not read.",
        "humanTracking": {"fleet": "https://fleet.re8ch.com", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_supabase_platform_status():
    namespace = "supabase-pg-re8ch"
    studio = probe_http("supabase-studio", "database-platform", "http://supabase-studio.supabase-pg-re8ch.svc.cluster.local:3000/", "Supabase human administration UI")
    deployments = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % namespace).get("items", [])
    endpoints = kube_request("GET", "/api/v1/namespaces/%s/endpoints" % namespace).get("items", [])
    workload_rows = []
    for item in deployments:
        meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
        workload_rows.append({
            "name": meta.get("name"),
            "desired": spec.get("replicas", 0),
            "ready": status.get("readyReplicas", 0),
            "available": status.get("availableReplicas", 0),
            "generation": meta.get("generation"),
            "observedGeneration": status.get("observedGeneration"),
        })
    workload_rows.sort(key=lambda item: item["name"] or "")
    endpoint_rows = []
    for item in endpoints:
        meta = item.get("metadata", {})
        ready = sum(len(subset.get("addresses", [])) for subset in item.get("subsets", []))
        not_ready = sum(len(subset.get("notReadyAddresses", [])) for subset in item.get("subsets", []))
        ports = sorted({port.get("port") for subset in item.get("subsets", []) for port in subset.get("ports", []) if port.get("port") is not None})
        endpoint_rows.append({"service": meta.get("name"), "readyAddresses": ready, "notReadyAddresses": not_ready, "ports": ports})
    endpoint_rows.sort(key=lambda item: item["service"] or "")
    return {
        "studio": studio,
        "summary": {
            "workloads": len(workload_rows),
            "readyWorkloads": sum(item["desired"] > 0 and item["ready"] >= item["desired"] for item in workload_rows),
            "unreadyWorkloads": sum(item["ready"] < item["desired"] for item in workload_rows),
            "servicesWithReadyEndpoints": sum(item["readyAddresses"] > 0 for item in endpoint_rows),
        },
        "workloads": workload_rows,
        "endpoints": endpoint_rows,
        "protectedNativeAPIs": ["Auth users/sessions", "PostgREST data/schema", "Storage objects/buckets", "Realtime channels", "Meta schema administration", "Kong routes/keys"],
        "authorization": "Supabase JWT/anon/service-role policies, PostgreSQL roles/RLS and component-native authorization remain authoritative; this tool does not proxy them.",
        "redaction": "JWT secrets, anon/service-role keys, database URLs, credentials, users, rows, buckets, objects and schema contents are not read.",
        "humanTracking": {"supabaseStudio": "https://supabase.re8ch.com", "cloudbeaver": CLOUDBEAVER_PUBLIC, "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_neo4j_platform_status():
    namespace = "neo4j"
    browser = probe_http("neo4j-browser", "database-platform", "http://neo4j.neo4j.svc.cluster.local:7474/", "Neo4j Browser/API reachability without graph credentials")
    statefulsets = kube_request("GET", "/apis/apps/v1/namespaces/%s/statefulsets" % namespace).get("items", [])
    endpoints = kube_request("GET", "/api/v1/namespaces/%s/endpoints" % namespace).get("items", [])
    workloads = []
    for item in statefulsets:
        meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
        desired = spec.get("replicas", 0)
        ready = status.get("readyReplicas", 0)
        workloads.append({
            "name": meta.get("name"),
            "desired": desired,
            "ready": ready,
            "current": status.get("currentReplicas", 0),
            "updated": status.get("updatedReplicas", 0),
            "scaledToZero": desired == 0,
            "unready": desired > ready,
        })
    workloads.sort(key=lambda item: item["name"] or "")
    endpoint_rows = []
    for item in endpoints:
        ready = sum(len(subset.get("addresses", [])) for subset in item.get("subsets", []))
        not_ready = sum(len(subset.get("notReadyAddresses", [])) for subset in item.get("subsets", []))
        endpoint_rows.append({"service": item.get("metadata", {}).get("name"), "readyAddresses": ready, "notReadyAddresses": not_ready})
    endpoint_rows.sort(key=lambda item: item["service"] or "")
    return {
        "browser": browser,
        "summary": {
            "instances": len(workloads),
            "readyInstances": sum(item["desired"] > 0 and item["ready"] >= item["desired"] for item in workloads),
            "unreadyInstances": sum(item["unready"] for item in workloads),
            "scaledToZeroInstances": sum(item["scaledToZero"] for item in workloads),
            "servicesWithReadyEndpoints": sum(item["readyAddresses"] > 0 for item in endpoint_rows),
        },
        "workloads": workloads,
        "endpoints": endpoint_rows,
        "protectedNativeAPIs": ["Bolt/Cypher queries", "transaction endpoints", "database/user/role administration", "graph data and schema"],
        "authorization": "Neo4j users, roles and database privileges remain authoritative; Kubernetes readiness does not grant graph access.",
        "redaction": "Neo4j credentials, Cypher text/results, nodes, relationships, properties, indexes, constraints and configuration are not read.",
        "humanTracking": {"neo4jBrowser": "https://neo4j.re8ch.com", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_identity_platform_status():
    started = time.monotonic()
    dex = probe_http("dex", "identity-control", "http://dex.dex.svc.cluster.local/.well-known/openid-configuration", "Dex OIDC discovery without identity credentials")
    gateway = probe_http("re8ch-auth-gateway", "identity-data", "http://re8ch-auth-gateway.auth.svc.cluster.local:8080/auth/healthz", "Application authentication gateway liveness")
    workloads = []
    endpoint_rows = []
    for namespace in ("dex", "auth"):
        deployments = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % namespace).get("items", [])
        endpoints = kube_request("GET", "/api/v1/namespaces/%s/endpoints" % namespace).get("items", [])
        for item in deployments:
            meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
            desired = spec.get("replicas", 0)
            ready = status.get("readyReplicas", 0)
            workloads.append({
                "namespace": namespace,
                "name": meta.get("name"),
                "desired": desired,
                "ready": ready,
                "available": status.get("availableReplicas", 0),
                "scaledToZero": desired == 0,
                "unready": desired > ready,
            })
        for item in endpoints:
            ready = sum(len(subset.get("addresses", [])) for subset in item.get("subsets", []))
            not_ready = sum(len(subset.get("notReadyAddresses", [])) for subset in item.get("subsets", []))
            endpoint_rows.append({"namespace": namespace, "service": item.get("metadata", {}).get("name"), "readyAddresses": ready, "notReadyAddresses": not_ready})
    workloads.sort(key=lambda item: (item["namespace"], item["name"] or ""))
    endpoint_rows.sort(key=lambda item: (item["namespace"], item["service"] or ""))
    return {
        "identityAuthority": "Dex",
        "probes": [dex, gateway],
        "summary": {
            "workloads": len(workloads),
            "readyWorkloads": sum(item["desired"] > 0 and item["ready"] >= item["desired"] for item in workloads),
            "unreadyWorkloads": sum(item["unready"] for item in workloads),
            "scaledToZeroWorkloads": sum(item["scaledToZero"] for item in workloads),
            "servicesWithReadyEndpoints": sum(item["readyAddresses"] > 0 for item in endpoint_rows),
        },
        "workloads": workloads,
        "endpoints": endpoint_rows,
        "protectedNativeAPIs": ["Dex users/groups", "static clients/connectors", "tokens/sessions", "OAuth/OIDC configuration"],
        "authorization": "Dex groups and application gateway identity checks remain authoritative; Kubernetes readiness is observation only.",
        "redaction": "Users, groups, sessions, tokens, client secrets, signing keys, credentials, policy contents and identity claims are not read.",
        "humanTracking": {"dex": "https://dex.re8ch.com", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_agent_runtime_status():
    namespaces = ("opensandbox-system", "codex-platform")
    workloads = []
    for namespace in namespaces:
        deployments = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % namespace).get("items", [])
        for item in deployments:
            meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
            desired = spec.get("replicas", 0)
            ready = status.get("readyReplicas", 0)
            workloads.append({
                "namespace": namespace,
                "name": meta.get("name"),
                "desired": desired,
                "ready": ready,
                "available": status.get("availableReplicas", 0),
                "scaledToZero": desired == 0,
                "unready": desired > ready,
            })
    workloads.sort(key=lambda item: (item["namespace"], item["name"] or ""))
    sandbox_kinds = {}
    for plural in ("batchsandboxes", "pools", "sandboxsnapshots"):
        try:
            items = kube_request("GET", "/apis/sandbox.opensandbox.io/v1alpha1/%s" % plural).get("items", [])
        except RuntimeError as exc:
            if "404" in str(exc) or "429" in str(exc):
                sandbox_kinds[plural] = {"available": False, "total": 0, "phases": {}}
                continue
            raise
        phases = {}
        for item in items:
            phase = item.get("status", {}).get("phase") or item.get("status", {}).get("state") or "unknown"
            phases[phase] = phases.get(phase, 0) + 1
        sandbox_kinds[plural] = {"available": True, "total": len(items), "phases": phases}
    probe_specs = [
        ("opensandbox", "agent-runtime", "http://opensandbox-server.opensandbox-system.svc.cluster.local:80/healthz", "OpenSandbox API reachability"),
        ("codex-session", "agent-runtime", "http://codex-session-web.codex-platform.svc.cluster.local:8080/healthz", "Codex session UI/API reachability"),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probe_specs)) as pool:
        probes = [future.result() for future in [pool.submit(probe_http, *spec) for spec in probe_specs]]
    return {
        "probes": probes,
        "summary": {
            "workloads": len(workloads),
            "readyWorkloads": sum(item["desired"] > 0 and item["ready"] >= item["desired"] for item in workloads),
            "unreadyWorkloads": sum(item["unready"] for item in workloads),
            "scaledToZeroWorkloads": sum(item["scaledToZero"] for item in workloads),
        },
        "workloads": workloads,
        "sandboxResources": sandbox_kinds,
        "protectedNativeAPIs": ["Agent prompts/memory/conversations", "model/provider credentials", "sandbox files/processes/commands/output", "Codex session contents and credentials"],
        "authorization": "OpenSandbox and Codex native APIs plus Kubernetes RBAC remain authoritative; workload readiness does not grant session or execution access.",
        "redaction": "Prompts, messages, memory, model configuration, tokens, credentials, sandbox names/specifications/files/processes/output and Codex session contents are not read.",
        "humanTracking": {"codex": "https://codex.re8ch.com", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_observability_platform_status():
    fixed_deployments = {
        "observability-system": {
            "re8ch-grafana", "re8ch-grafana-renderer", "vmalert-re8ch",
            "vmselect-re8ch-metrics", "vlinsert-re8ch-logs", "vlselect-re8ch-logs",
        },
        "observability-ai": {"robusta-forwarder", "robusta-runner", "trial-gateway-collector"},
        "opencost": {"opencost"},
    }
    workloads = []
    for namespace, names in fixed_deployments.items():
        items = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % namespace).get("items", [])
        for item in items:
            meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
            if meta.get("name") not in names:
                continue
            desired, ready = spec.get("replicas", 0), status.get("readyReplicas", 0)
            workloads.append({"kind": "Deployment", "namespace": namespace, "name": meta.get("name"), "desired": desired, "ready": ready, "available": status.get("availableReplicas", 0), "unready": desired > ready})
    daemonsets = kube_request("GET", "/apis/apps/v1/namespaces/qianwen-ops/daemonsets").get("items", [])
    for item in daemonsets:
        meta, status = item.get("metadata", {}), item.get("status", {})
        if meta.get("name") not in {"alloy-nuclio-logs", "panel-harbor-node-vmagent", "panel-node-exporter", "panel-qwen-functions-vmagent", "panel-vlagent"}:
            continue
        desired, ready = status.get("desiredNumberScheduled", 0), status.get("numberReady", 0)
        workloads.append({"kind": "DaemonSet", "namespace": "qianwen-ops", "name": meta.get("name"), "desired": desired, "ready": ready, "available": status.get("numberAvailable", 0), "unready": desired > ready})
    workloads.sort(key=lambda item: (item["namespace"], item["kind"], item["name"] or ""))
    probe_specs = [
        ("grafana", "observability-control", "http://re8ch-grafana.observability-system.svc.cluster.local:3000/grafana/api/health", "Grafana API/database health"),
        ("victoria-metrics", "observability-data", "http://vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus/-/healthy", "VictoriaMetrics health"),
        ("vmalert-re8ch", "observability-control", "http://vmalert-re8ch.observability-system.svc.cluster.local:8080/health", "platform VMAlert health"),
        ("victoria-logs", "observability-data", "http://vlsingle-re8ch-logs.observability-system.svc.cluster.local:9428/health", "VictoriaLogs health"),
        ("opencost", "cost-data", "http://opencost.opencost.svc.cluster.local:9003/healthz", "OpenCost API health"),
        ("otel-collector", "telemetry-control", "http://re8ch-gateway-collector-monitoring.observability-system.svc.cluster.local:8888/metrics", "OTLP Collector self-metrics reachability"),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probe_specs)) as pool:
        probes = [future.result() for future in [pool.submit(probe_http, *spec) for spec in probe_specs]]
    metrics = {"totalTargets": None, "downTargets": None, "error": None}
    try:
        for field, expression in (("totalTargets", "count(up)"), ("downTargets", "count(up == 0)")):
            url = "http://vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": expression})
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "re8ch-ops-mcp-observe/1.0"}), timeout=6) as response:
                parsed = json.loads(response.read(65536).decode("utf-8", "replace"))
            result = parsed.get("data", {}).get("result", [])
            metrics[field] = int(float(result[0]["value"][1])) if result else 0
    except Exception as exc:
        metrics["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    cluster_cost = {"window": "1h", "available": False, "error": None}
    try:
        url = "http://opencost.opencost.svc.cluster.local:9003/allocation/compute?window=1h&aggregate=cluster"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "re8ch-ops-mcp-observe/1.0"}), timeout=10) as response:
            parsed = json.loads(response.read(1048576).decode("utf-8", "replace"))
        data = parsed.get("data", [])
        allocation = next(iter(data[0].values())) if data and isinstance(data[0], dict) and data[0] else None
        if allocation:
            cluster_cost.update({
                "available": True, "start": allocation.get("start"), "end": allocation.get("end"),
                "cpuCoreUsageAverage": allocation.get("cpuCoreUsageAverage"), "cpuCoreRequestAverage": allocation.get("cpuCoreRequestAverage"),
                "cpuEfficiency": allocation.get("cpuEfficiency"), "ramByteUsageAverage": allocation.get("ramByteUsageAverage"),
                "ramByteRequestAverage": allocation.get("ramByteRequestAverage"), "ramEfficiency": allocation.get("ramEfficiency"),
                "totalEfficiency": allocation.get("totalEfficiency"), "totalCost": allocation.get("totalCost"),
            })
    except Exception as exc:
        cluster_cost["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return {
        "probes": probes,
        "summary": {"workloads": len(workloads), "readyWorkloads": sum(not item["unready"] for item in workloads), "unreadyWorkloads": sum(item["unready"] for item in workloads)},
        "workloads": workloads,
        "metricsTargets": metrics,
        "clusterCostOneHour": cluster_cost,
        "protectedNativeAPIs": ["arbitrary PromQL/LogsQL", "raw logs/traces/profiles", "alert labels/annotations/history", "Grafana dashboards/datasources/users/service accounts", "namespace/workload cost detail", "Robusta findings and destinations"],
        "authorization": "Grafana organizations/service accounts, tenant metrics/log proxies, OpenCost OAuth, Robusta destinations and Kubernetes RBAC remain authoritative.",
        "redaction": "Datasource credentials, arbitrary queries, raw series labels, logs, traces, alert contents, tenant/workload costs, Robusta findings and notification destinations are not read.",
        "humanTracking": {"grafana": GRAFANA_PUBLIC, "openCost": "https://api-shanghai217.re8ch.com/opencost/", "headlamp": HEADLAMP_PUBLIC},
    }

def get_artifact_platform_status():
    workloads = []
    for namespace in ("harbor", "image"):
        deployments = kube_request("GET", "/apis/apps/v1/namespaces/%s/deployments" % namespace).get("items", [])
        for item in deployments:
            meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
            desired, ready = spec.get("replicas", 0), status.get("readyReplicas", 0)
            workloads.append({"namespace": namespace, "name": meta.get("name"), "desired": desired, "ready": ready, "available": status.get("availableReplicas", 0), "scaledToZero": desired == 0, "unready": desired > ready})
    workloads.sort(key=lambda item: (item["namespace"], item["name"] or ""))
    health_probe = probe_http("harbor-core", "artifact-control", HARBOR_HEALTH_URL, "Harbor core component health")
    harbor_health = {"status": None, "components": [], "error": None}
    try:
        request = urllib.request.Request(HARBOR_HEALTH_URL, headers={"User-Agent": "re8ch-ops-mcp-observe/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            parsed = json.loads(response.read(65536).decode("utf-8", "replace"))
        harbor_health["status"] = parsed.get("status")
        harbor_health["components"] = sorted(
            [{"name": item.get("name"), "status": item.get("status")} for item in parsed.get("components", [])],
            key=lambda item: item["name"] or "",
        )
    except Exception as exc:
        harbor_health["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return {
        "probe": health_probe,
        "harborHealth": harbor_health,
        "summary": {
            "workloads": len(workloads),
            "readyWorkloads": sum(item["desired"] > 0 and item["ready"] >= item["desired"] for item in workloads),
            "unreadyWorkloads": sum(item["unready"] for item in workloads),
            "scaledToZeroWorkloads": sum(item["scaledToZero"] for item in workloads),
            "healthyHarborComponents": sum(item["status"] == "healthy" for item in harbor_health["components"]),
        },
        "workloads": workloads,
        "publicationFlow": ["build/sign", "push to tenant project", "scan", "approve/promote", "deploy by digest", "observe rollout"],
        "protectedNativeAPIs": ["project/repository contents", "artifact manifests/layers", "vulnerability findings", "replication jobs", "users/members/robots", "retention/scanner configuration"],
        "authorization": "Harbor project roles, repository permissions, robot scopes, scanner policy and Kubernetes image-pull authorization remain authoritative.",
        "redaction": "Robot secrets, registry credentials, image-pull Secrets, artifact manifests/layers, SBOMs, vulnerability details and user/member data are not read.",
        "humanTracking": {"harbor": REGISTRY_PUBLIC, "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def get_database_routing_status():
    endpoint_slices = kube_request("GET", "/apis/discovery.k8s.io/v1/namespaces/pigsty-external/endpointslices").get("items", [])
    slice_rows = []
    for item in endpoint_slices:
        meta = item.get("metadata", {})
        endpoints = item.get("endpoints", [])
        ready = sum(endpoint.get("conditions", {}).get("ready") is not False for endpoint in endpoints)
        not_ready = len(endpoints) - ready
        slice_rows.append({
            "service": meta.get("labels", {}).get("kubernetes.io/service-name") or meta.get("name"),
            "dataRole": meta.get("labels", {}).get("re8ch.com/data-role"),
            "readyEndpoints": ready,
            "notReadyEndpoints": not_ready,
            "ports": sorted({port.get("name") for port in item.get("ports", []) if port.get("name")}),
        })
    slice_rows.sort(key=lambda item: item["service"] or "")
    probe_specs = []
    for route, details in PG_SERVICE_ROUTES.items():
        if details.get("host"):
            probe_specs.append((probe_tcp, ("pigsty-%s" % route, "database-routing", details["host"], details["port"], details["purpose"])))
        else:
            probe_specs.append((probe_http, ("pigsty-%s" % route, "database-routing", details["url"], details["purpose"])))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probe_specs)) as pool:
        route_probes = [future.result() for future in [pool.submit(fn, *args) for fn, args in probe_specs]]
    cloudbeaver = probe_http("cloudbeaver", "database-control", CLOUDBEAVER_PUBLIC, "human SQL administration UI reachability")
    return {
        "routes": sorted(route_probes, key=lambda item: item["name"]),
        "endpointSlices": slice_rows,
        "cloudbeaver": cloudbeaver,
        "summary": {
            "routes": len(route_probes),
            "reachableRoutes": sum(item["status"] in {"healthy", "reachable"} for item in route_probes),
            "unreachableRoutes": sum(item["status"] not in {"healthy", "reachable"} for item in route_probes),
            "serviceRoutes": len(slice_rows),
            "serviceRoutesWithReadyEndpoint": sum(item["readyEndpoints"] > 0 for item in slice_rows),
        },
        "routePolicy": {
            "write": "pgwrite HA service for migrations/admin/runtime writes; direct instance only by explicit DBA choice",
            "read": "regional pgread service after readiness verification",
            "pool": "PgBouncer for high-churn clients after role compatibility verification",
            "http": "PostgREST gateway with database roles/RLS; HTTP reachability does not grant row access",
        },
        "protectedNativeAPIs": ["arbitrary SQL", "database/schema/table/row contents", "PostgREST OpenAPI schema", "roles/grants", "CloudBeaver connections/users", "Patroni/PgBouncer administration"],
        "authorization": "PostgreSQL roles/database grants/RLS, PgBouncer authentication, PostgREST JWT/database roles, CloudBeaver roles and Kubernetes Secret RBAC remain authoritative.",
        "redaction": "Database URLs with credentials, passwords, Secret values, SQL, schemas, tables, rows, JWTs and CloudBeaver connection definitions are not read.",
        "humanTracking": {"cloudbeaver": CLOUDBEAVER_PUBLIC, "grafana": GRAFANA_PUBLIC, "headlamp": HEADLAMP_PUBLIC},
    }

def get_external_publication_readiness():
    mcp_origin = os.getenv("EXTERNAL_TOOLS_ORIGIN", "https://tools.re8ch.com:8443").rstrip("/")
    mcp_resource = mcp_origin + "/mcp"
    auth_origin = os.getenv("EXTERNAL_AUTH_ORIGIN", mcp_origin).rstrip("/")
    upstream_auth_origin = os.getenv("UPSTREAM_AUTH_ORIGIN", "https://auth.re8ch.com").rstrip("/")

    def dns_status(host):
        try:
            addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
            return {"resolved": bool(addresses), "addressCount": len(addresses), "error": None}
        except Exception as exc:
            return {"resolved": False, "addressCount": 0, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}

    def tls_status(host, port=443):
        try:
            context = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=host) as secured:
                    certificate = secured.getpeercert()
            return {"trusted": True, "expires": certificate.get("notAfter"), "error": None}
        except Exception as exc:
            return {"trusted": False, "expires": None, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}

    def json_metadata(url, validator):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "re8ch-ops-mcp-publication-readiness/1.0"})
            with urllib.request.urlopen(request, timeout=6) as response:
                parsed = json.loads(response.read(65536).decode("utf-8", "replace"))
            return {"valid": bool(validator(parsed)), "httpStatus": response.status, "error": None}
        except Exception as exc:
            return {"valid": False, "httpStatus": getattr(exc, "code", None), "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}

    tools_url, upstream_auth_url = urllib.parse.urlparse(mcp_origin), urllib.parse.urlparse(upstream_auth_origin)
    tools_host, upstream_auth_host = tools_url.hostname or "tools.re8ch.com", upstream_auth_url.hostname or "auth.re8ch.com"
    tools_dns, auth_dns = dns_status(tools_host), dns_status(upstream_auth_host)
    tools_tls = tls_status(tools_host, tools_url.port or 443)
    auth_tls = tls_status(upstream_auth_host, upstream_auth_url.port or 443)
    resource_metadata = json_metadata(
        mcp_origin + "/.well-known/oauth-protected-resource/mcp",
        lambda value: value.get("resource") == mcp_resource and bool(value.get("authorization_servers")),
    )
    oauth_metadata = {"valid": False, "httpStatus": None, "error": "no valid OAuth/OIDC discovery document"}
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        candidate = json_metadata(
            auth_origin + path,
            lambda value: bool(value.get("issuer")) and str(value.get("authorization_endpoint", "")).startswith("https://") and str(value.get("token_endpoint", "")).startswith("https://") and "S256" in value.get("code_challenge_methods_supported", []),
        )
        if candidate["valid"]:
            candidate["path"] = path
            oauth_metadata = candidate
            break
        oauth_metadata = candidate
    challenge = {"valid": False, "httpStatus": None, "resourceMetadataAdvertised": False, "error": None}
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "readiness", "version": "1"}},
        }).encode("utf-8")
        request = urllib.request.Request(mcp_resource, data=payload, method="POST", headers={
            "User-Agent": "re8ch-ops-mcp-publication-readiness/2.0",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        urllib.request.urlopen(request, timeout=6)
        challenge["error"] = "unauthenticated request unexpectedly succeeded"
    except urllib.error.HTTPError as exc:
        header = exc.headers.get("WWW-Authenticate", "")
        challenge.update({"httpStatus": exc.code, "resourceMetadataAdvertised": "resource_metadata=" in header, "valid": exc.code == 401 and "resource_metadata=" in header})
    except Exception as exc:
        challenge["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    external_server = False
    try:
        kube_request("GET", "/apis/toolhive.stacklok.dev/v1beta1/namespaces/toolhive-system/mcpservers/ops-observe-external")
        external_server = True
    except KubeError as exc:
        if exc.status != 404:
            raise
    ingresses = kube_request("GET", "/apis/networking.k8s.io/v1/ingresses").get("items", [])
    public_ingress = any(
        rule.get("host") == "tools.re8ch.com"
        for item in ingresses for rule in item.get("spec", {}).get("rules", [])
    )
    gates = {
        "toolsDNS": tools_dns["resolved"], "authDNS": auth_dns["resolved"],
        "toolsTLS": tools_tls["trusted"], "authTLS": auth_tls["trusted"],
        "protectedResourceMetadata": resource_metadata["valid"], "oauthDiscoveryPKCE": oauth_metadata["valid"],
        "unauthenticatedChallenge": challenge["valid"], "externalMCPServer": external_server,
        "publicIngress": public_ingress,
    }
    return {
        "publicationReady": all(gates.values()),
        "gates": gates,
        "blockers": sorted(name for name, passed in gates.items() if not passed),
        "dns": {"tools": tools_dns, "auth": auth_dns},
        "tls": {"tools": tools_tls, "auth": auth_tls},
        "protectedResourceMetadata": resource_metadata,
        "oauthDiscovery": oauth_metadata,
        "mcpChallenge": challenge,
        "origins": {"mcp": mcp_origin, "authorizationServer": auth_origin, "upstreamIdentity": upstream_auth_origin},
        "policy": "The published restricted-node-6dc3f140f9 endpoint is ready only when DNS/TLS, embedded OAuth metadata, the MCP authentication challenge and cluster publication objects all pass.",
        "redaction": "OAuth client secrets, access/refresh tokens, certificate bodies, DNS API credentials and user claims are not read.",
        "humanTracking": {"toolhiveRegistry": "internal ToolHive Registry", "headlamp": HEADLAMP_PUBLIC, "grafana": GRAFANA_PUBLIC},
    }

def tenant_context():
    return {
        "tenant": current_tenant(),
        "namespace": current_namespace(),
        "kubeServer": KUBE_SERVER_PUBLIC,
        "headlamp": HEADLAMP_PUBLIC,
        "grafana": GRAFANA_PUBLIC,
        "registry": REGISTRY_PUBLIC,
        "registryAlternate": REGISTRY_ALT,
        "registryProject": current_harbor_project(),
        "postgresRecommendedRoutes": PG_SERVICE_ROUTES,
        "allowedOperations": ["list tenant resources", "read nodes", "read metrics", "restart tenant deployments", "download tenant kubeconfig"],
    }

def registry_info():
    project = current_harbor_project()
    return {
        "primary": REGISTRY_PUBLIC,
        "alternate": REGISTRY_ALT,
        "tenantNamespace": current_namespace(),
        "projectPath": "/%s" % project,
        "k8sPullSecret": current_harbor_secret(),
        "pullRobotUsername": current_harbor_robot(),
        "examples": [
            "docker login registry.re8ch.com",
            "docker pull registry.re8ch.com/%s/<image>:<tag>" % project,
            "kubectl -n %s get secret %s" % (current_namespace(), current_harbor_secret()),
        ],
    }

def grafana_links():
    return {
        "grafana": GRAFANA_PUBLIC,
        "purpose": "metrics and observability dashboards",
        "tenantDashboard": current_grafana_dashboard(),
        "suggestedUse": ["inspect service health", "check node and pod trends", "compare deployment impact"],
    }


def probe_tcp(name, domain, host, port, purpose):
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            status = "reachable"
            error = ""
    except Exception as exc:
        status = "unreachable"
        error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return {
        "name": name,
        "domain": domain,
        "purpose": purpose,
        "endpoint": "%s:%s" % (host, port),
        "status": status,
        "latencyMs": int((time.monotonic() - started) * 1000),
        "error": error,
    }


def probe_http(name, domain, url, purpose):
    started = time.monotonic()
    status_code = None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "re8ch-ops-mcp-health/1.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            status_code = response.status
        status = "healthy" if status_code < 500 else "degraded"
        error = ""
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        status = "reachable" if exc.code < 500 else "degraded"
        error = "HTTP %s" % exc.code
    except Exception as exc:
        status = "unreachable"
        error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return {
        "name": name,
        "domain": domain,
        "purpose": purpose,
        "endpoint": url,
        "status": status,
        "httpStatus": status_code,
        "latencyMs": int((time.monotonic() - started) * 1000),
        "error": error,
    }


def get_platform_service_health():
    """Probe fixed platform endpoints without accepting arbitrary hosts or URLs."""
    catalog = current_catalog()
    cloudbeaver = catalog.get("cloudbeaver", {}).get("url") or CLOUDBEAVER_PUBLIC
    probes = [
        (probe_http, ("harbor-registry", "artifact", HARBOR_HEALTH_URL, "Harbor internal API health; human URL is reported separately")),
        (probe_http, ("grafana", "observability", GRAFANA_PUBLIC, "human dashboards and tool telemetry")),
        (probe_http, ("headlamp", "kubernetes", HEADLAMP_PUBLIC, "human Kubernetes control GUI")),
        (probe_http, ("cloudbeaver", "database", cloudbeaver, "human tenant SQL GUI")),
        (probe_http, ("toolhive-registry", "tool-control", TOOLHIVE_REGISTRY_URL, "approved MCP catalog")),
        (probe_http, ("dex", "identity-control", "http://dex.dex.svc.cluster.local/.well-known/openid-configuration", "Dex OIDC discovery")),
        (probe_http, ("litellm", "model-control", "http://litellm.llm-gateway.svc.cluster.local:4000/health/liveliness", "LiteLLM model gateway liveness; no provider credentials")),
        (probe_http, ("opensandbox", "agent-runtime", "http://opensandbox-server.opensandbox-system.svc.cluster.local:80/healthz", "OpenSandbox API reachability")),
        (probe_http, ("fleet", "device-control", "http://fleet.fleet.svc.cluster.local:8080/healthz", "Fleet device-control liveness")),
        (probe_http, ("neo4j", "database-platform", "http://neo4j.neo4j.svc.cluster.local:7474/", "Neo4j HTTP endpoint reachability; no graph credentials")),
        (probe_http, ("supabase-studio", "database-platform", "http://supabase-studio.supabase-pg-re8ch.svc.cluster.local:3000/", "Supabase human control GUI reachability")),
        (probe_http, ("nuclio-dashboard", "application-control", "http://nuclio-dashboard.nuclio.svc.cluster.local:8070/api/healthz", "Nuclio application-control endpoint reachability")),
        (probe_http, ("opencost", "observability-data", "http://opencost.opencost.svc.cluster.local:9003/healthz", "OpenCost allocation service liveness")),
        (probe_http, ("codex-session", "agent-runtime", "http://codex-session-web.codex-platform.svc.cluster.local:8080/healthz", "Codex session runtime liveness")),
    ]
    for route, details in PG_SERVICE_ROUTES.items():
        if details.get("host"):
            parsed = urllib.parse.urlparse("tcp://%s:%s" % (details["host"], details["port"]))
        else:
            parsed = urllib.parse.urlparse("tcp://" + details["url"].split("://", 1)[-1])
        probes.append((probe_tcp, ("pigsty-%s" % route, "database", parsed.hostname, parsed.port, details["purpose"])))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(probes))) as pool:
        futures = [pool.submit(fn, *args) for fn, args in probes]
        results = [future.result() for future in futures]
    healthy_states = {"reachable", "healthy"}
    healthy = sum(1 for item in results if item["status"] in healthy_states)
    return {
        "tenant": current_tenant(),
        "summary": {"reachable": healthy, "total": len(results), "unreachable": len(results) - healthy},
        "services": sorted(results, key=lambda item: (item["domain"], item["name"])),
        "meaning": "Reachability is a control-path signal, not proof of application correctness or authorization.",
        "humanTracking": {
            "grafana": GRAFANA_PUBLIC,
            "headlamp": HEADLAMP_PUBLIC,
            "harbor": REGISTRY_PUBLIC,
            "cloudbeaver": cloudbeaver,
        },
    }

def get_platform_service_catalog():
    """Return a credential-free, live inventory with a default permission model."""
    resources = {
        "namespaces": kube_request("GET", "/api/v1/namespaces").get("items", []),
        "services": kube_request("GET", "/api/v1/services").get("items", []),
        "deployments": kube_request("GET", "/apis/apps/v1/deployments").get("items", []),
        "statefulsets": kube_request("GET", "/apis/apps/v1/statefulsets").get("items", []),
        "daemonsets": kube_request("GET", "/apis/apps/v1/daemonsets").get("items", []),
        "ingresses": kube_request("GET", "/apis/networking.k8s.io/v1/ingresses").get("items", []),
    }
    by_namespace = {}
    for item in resources["namespaces"]:
        metadata = item.get("metadata", {})
        name = metadata.get("name")
        if name and name not in SYSTEM_NAMESPACES:
            domain, gui = PLATFORM_NAMESPACE_DOMAINS.get(name, ("application-data", "Headlamp, Grafana"))
            control_plane = domain.endswith("control") or domain in {"cluster-control", "human-control"}
            plane = "control" if control_plane else "data"
            explicitly_classified = name in PLATFORM_NAMESPACE_DOMAINS
            labels = metadata.get("labels", {})
            by_namespace[name] = {
                "namespace": name,
                "domain": domain,
                "plane": plane,
                "explicitPolicy": explicitly_classified,
                "materializedPolicy": {
                    "domain": labels.get("re8ch.com/domain", ""),
                    "plane": labels.get("re8ch.com/plane", ""),
                    "version": labels.get("re8ch.com/access-policy", ""),
                    "matches": labels.get("re8ch.com/domain") == domain and labels.get("re8ch.com/plane") == plane and labels.get("re8ch.com/access-policy") == "toolhive-v1",
                },
                "workloads": [],
                "services": [],
                "ingressHosts": [],
                "agentPermissions": {
                    "observe": "metadata/status/log-derived metrics; no Secrets",
                    "operate": "named workload actions only, namespace-scoped and audited",
                    "admin": "native controller/API role plus approval ID; never generic proxy by default",
                },
                "nativeAuthority": DOMAIN_NATIVE_AUTHORITY.get(domain, "namespace RBAC and application-native authorization"),
                "humanTracking": [value.strip() for value in gui.split(",")],
            }
    for kind in ("deployments", "statefulsets", "daemonsets"):
        label = kind[:-1].capitalize() if kind != "statefulsets" else "StatefulSet"
        if kind == "daemonsets":
            label = "DaemonSet"
        for item in resources[kind]:
            meta = item.get("metadata", {})
            spec, status = item.get("spec", {}), item.get("status", {})
            entry = by_namespace.get(meta.get("namespace"))
            if entry is not None:
                desired = spec.get("replicas")
                if kind == "daemonsets":
                    desired = status.get("desiredNumberScheduled")
                    ready = status.get("numberReady", 0)
                    available = status.get("numberAvailable", 0)
                else:
                    ready = status.get("readyReplicas", 0)
                    available = status.get("availableReplicas", 0)
                entry["workloads"].append({
                    "kind": label,
                    "name": meta.get("name"),
                    "desired": desired if desired is not None else 0,
                    "ready": ready,
                    "available": available,
                    "scaledToZero": desired == 0,
                })
    for item in resources["services"]:
        meta, spec = item.get("metadata", {}), item.get("spec", {})
        entry = by_namespace.get(meta.get("namespace"))
        if entry is not None:
            entry["services"].append({
                "name": meta.get("name"),
                "type": spec.get("type", "ClusterIP"),
                "ports": sorted({port.get("port") for port in spec.get("ports", []) if port.get("port") is not None}),
            })
    for item in resources["ingresses"]:
        meta, spec = item.get("metadata", {}), item.get("spec", {})
        entry = by_namespace.get(meta.get("namespace"))
        if entry is not None:
            entry["ingressHosts"].extend(rule.get("host") for rule in spec.get("rules", []) if rule.get("host"))
    entries = []
    for entry in by_namespace.values():
        entry["workloads"].sort(key=lambda value: (value["kind"], value["name"] or ""))
        entry["services"].sort(key=lambda value: value["name"] or "")
        entry["ingressHosts"] = sorted(set(entry["ingressHosts"]))
        entries.append(entry)
    entries.sort(key=lambda value: value["namespace"])
    classified = sum(1 for entry in entries if entry["namespace"] in PLATFORM_NAMESPACE_DOMAINS)
    return {
        "generatedAt": int(time.time()),
        "source": "live Kubernetes API; no Secret values",
        "summary": {
            "namespaces": len(entries),
            "explicitlyClassified": classified,
            "defaultApplicationData": len(entries) - classified,
            "nativeAuthorityMapped": sum(bool(entry.get("nativeAuthority")) for entry in entries),
            "materializedPolicyMatched": sum(entry["materializedPolicy"]["matches"] for entry in entries),
            "workloads": sum(len(entry["workloads"]) for entry in entries),
            "services": sum(len(entry["services"]) for entry in entries),
            "ingressHosts": len({host for entry in entries for host in entry["ingressHosts"]}),
        },
        "defaultPolicy": {
            "observe": "read-only inventory/status and fixed metrics; no credentials",
            "operate": "allowlisted namespace-scoped action with downstream RBAC",
            "admin": "separate identity, approval, native authorization, audit and human GUI evidence",
        },
        "namespaces": entries,
    }

def get_platform_permission_coverage():
    """Return the effective AI/native/GUI decision for every live workload and Service."""
    catalog = get_platform_service_catalog()
    resources = []
    for namespace_entry in catalog["namespaces"]:
        namespace = namespace_entry["namespace"]
        domain = namespace_entry["domain"]
        native_authority = namespace_entry["nativeAuthority"]
        human_tracking = namespace_entry["humanTracking"]
        for workload in namespace_entry["workloads"]:
            target = (namespace, workload["name"])
            tenant_restart = workload["kind"] == "Deployment" and namespace in TENANT_NAMESPACES
            managed_admin = workload["kind"] == "Deployment" and target in MANAGED_PLATFORM_RESTARTS
            if managed_admin:
                admin_policy = "approval-gated restart/scale through named ToolHive admin tools"
            else:
                admin_policy = "native control plane only; no default ToolHive mutation published"
            resources.append({
                "resourceType": "workload",
                "namespace": namespace,
                "kind": workload["kind"],
                "name": workload["name"],
                "domain": domain,
                "plane": namespace_entry["plane"],
                "runtime": {
                    "desired": workload["desired"],
                    "ready": workload["ready"],
                    "available": workload["available"],
                    "scaledToZero": workload["scaledToZero"],
                },
                "aiAccess": {
                    "observe": "published: sanitized metadata/readiness; Secrets and workload configuration omitted",
                    "operate": "tenant-scoped restart through operate surface" if tenant_restart else "not published on operate surface",
                    "admin": admin_policy,
                },
                "nativeAuthority": native_authority,
                "humanTracking": human_tracking,
                "decisionSource": "live Kubernetes inventory + checked-in namespace classification + exact ToolHive operation allowlists",
            })
        for service in namespace_entry["services"]:
            resources.append({
                "resourceType": "service",
                "namespace": namespace,
                "kind": "Service",
                "name": service["name"],
                "domain": domain,
                "plane": namespace_entry["plane"],
                "exposure": {"type": service["type"], "ports": service["ports"]},
                "aiAccess": {
                    "observe": "published: name/type/ports and fixed reviewed health probes where defined",
                    "operate": "not published; traffic and endpoint mutation require native review",
                    "admin": "native control plane only; no generic Service mutation tool",
                },
                "nativeAuthority": native_authority,
                "humanTracking": human_tracking,
                "decisionSource": "live Kubernetes inventory + checked-in namespace classification",
            })
    resources.sort(key=lambda value: (value["namespace"], value["resourceType"], value["kind"], value["name"] or ""))
    unclassified = sorted(entry["namespace"] for entry in catalog["namespaces"] if not entry["explicitPolicy"])
    policy_label_mismatches = sorted(entry["namespace"] for entry in catalog["namespaces"] if not entry["materializedPolicy"]["matches"])
    missing_gui = sorted({item["namespace"] for item in resources if not item["humanTracking"]})
    missing_authority = sorted({item["namespace"] for item in resources if not item["nativeAuthority"]})
    workload_count = sum(item["resourceType"] == "workload" for item in resources)
    service_count = sum(item["resourceType"] == "service" for item in resources)
    managed_admin_count = sum(
        item["resourceType"] == "workload" and item["aiAccess"]["admin"].startswith("approval-gated")
        for item in resources
    )
    tenant_operate_count = sum(
        item["resourceType"] == "workload" and item["aiAccess"]["operate"].startswith("tenant-scoped")
        for item in resources
    )
    return {
        "generatedAt": int(time.time()),
        "source": "live Kubernetes API plus checked-in ToolHive policy; no Secret values, Pod specs, environment or application data",
        "coverageComplete": not unclassified and not policy_label_mismatches and not missing_gui and not missing_authority,
        "summary": {
            "namespaces": catalog["summary"]["namespaces"],
            "workloads": workload_count,
            "services": service_count,
            "resourceDecisions": len(resources),
            "managedAdminWorkloads": managed_admin_count,
            "tenantOperateWorkloads": tenant_operate_count,
            "unclassifiedNamespaces": len(unclassified),
            "policyLabelMismatches": len(policy_label_mismatches),
            "namespacesMissingHumanTracking": len(missing_gui),
            "namespacesMissingNativeAuthority": len(missing_authority),
        },
        "gaps": {
            "unclassifiedNamespaces": unclassified,
            "policyLabelMismatches": policy_label_mismatches,
            "namespacesMissingHumanTracking": missing_gui,
            "namespacesMissingNativeAuthority": missing_authority,
        },
        "enforcement": {
            "publication": "ToolHive profile allowlists and Registry stage",
            "workloadMutation": "Kubernetes resourceNames RBAC plus tool target allowlist and approval ID",
            "domainData": "application/database/registry/identity native authorization remains final",
            "humanEvidence": "Headlamp/Grafana plus the listed domain GUI; human and Agent identities stay separate",
        },
        "resources": resources,
    }

def get_platform_access_posture():
    """Summarize namespace authorization and exposure controls without credentials."""
    namespace_items = kube_request("GET", "/api/v1/namespaces").get("items", [])
    roles = kube_request("GET", "/apis/rbac.authorization.k8s.io/v1/roles").get("items", [])
    bindings = kube_request("GET", "/apis/rbac.authorization.k8s.io/v1/rolebindings").get("items", [])
    accounts = kube_request("GET", "/api/v1/serviceaccounts").get("items", [])
    policies = kube_request("GET", "/apis/networking.k8s.io/v1/networkpolicies").get("items", [])
    services = kube_request("GET", "/api/v1/services").get("items", [])
    ingresses = kube_request("GET", "/apis/networking.k8s.io/v1/ingresses").get("items", [])
    namespaces = sorted(
        item.get("metadata", {}).get("name") for item in namespace_items
        if item.get("metadata", {}).get("name") not in SYSTEM_NAMESPACES
    )
    entries = []
    for namespace in namespaces:
        ns_roles = [item for item in roles if item.get("metadata", {}).get("namespace") == namespace]
        ns_bindings = [item for item in bindings if item.get("metadata", {}).get("namespace") == namespace]
        ns_accounts = [item for item in accounts if item.get("metadata", {}).get("namespace") == namespace]
        ns_policies = [item for item in policies if item.get("metadata", {}).get("namespace") == namespace]
        ns_services = [item for item in services if item.get("metadata", {}).get("namespace") == namespace]
        ns_ingresses = [item for item in ingresses if item.get("metadata", {}).get("namespace") == namespace]
        default_account = next((item for item in ns_accounts if item.get("metadata", {}).get("name") == "default"), {})
        default_automount = default_account.get("automountServiceAccountToken")
        nodeports = sorted(
            item.get("metadata", {}).get("name") for item in ns_services
            if item.get("spec", {}).get("type") in {"NodePort", "LoadBalancer"}
        )
        findings = []
        if not ns_roles:
            findings.append("no-namespaced-roles")
        if not ns_bindings:
            findings.append("no-namespaced-rolebindings")
        if not ns_policies:
            findings.append("no-networkpolicy")
        if default_automount is not False:
            findings.append("default-serviceaccount-token-not-disabled")
        if nodeports:
            findings.append("nodeport-or-loadbalancer-exposure")
        if ns_ingresses:
            findings.append("ingress-exposure")
        domain = PLATFORM_NAMESPACE_DOMAINS.get(namespace, ("application-data", "Headlamp, Grafana"))[0]
        entries.append({
            "namespace": namespace,
            "domain": domain,
            "counts": {
                "roles": len(ns_roles),
                "roleBindings": len(ns_bindings),
                "serviceAccounts": len(ns_accounts),
                "networkPolicies": len(ns_policies),
                "ingresses": len(ns_ingresses),
            },
            "defaultServiceAccountAutomount": default_automount if default_automount is not None else "inherited-true",
            "externallyExposedServices": nodeports,
            "findings": findings,
            "humanReview": "Headlamp for RBAC/network objects; Grafana for traffic; domain GUI for application authorization",
        })
    return {
        "generatedAt": int(time.time()),
        "source": "live Kubernetes RBAC, ServiceAccount, NetworkPolicy, Service and Ingress metadata; no Secrets",
        "summary": {
            "namespaces": len(entries),
            "withoutRoleBindings": sum("no-namespaced-rolebindings" in item["findings"] for item in entries),
            "withoutNetworkPolicy": sum("no-networkpolicy" in item["findings"] for item in entries),
            "defaultTokenNotDisabled": sum("default-serviceaccount-token-not-disabled" in item["findings"] for item in entries),
            "withNodePortOrLoadBalancer": sum("nodeport-or-loadbalancer-exposure" in item["findings"] for item in entries),
            "withIngress": sum("ingress-exposure" in item["findings"] for item in entries),
        },
        "interpretation": "Findings are review signals, not automatic vulnerabilities. Native application roles remain authoritative behind Kubernetes exposure controls.",
        "namespaces": entries,
    }


MANAGED_TOOL_PUBLICATIONS = {
    "ops-observe-managed",
    "ops-operate-managed",
    "ops-admin-managed",
}
PUBLICATION_STAGES = {"canary", "internal", "deprecated"}


def set_tool_publication_stage(args):
    args = args or {}
    server = str(args.get("server") or "").strip()
    stage = str(args.get("stage") or "").strip().lower()
    if server not in MANAGED_TOOL_PUBLICATIONS:
        raise ValueError("server is not an approved managed publication: %s" % server)
    if stage not in PUBLICATION_STAGES:
        raise ValueError("stage must be one of: canary, internal, deprecated")
    exported = "false" if stage == "deprecated" else "true"
    changed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    patch = {
        "metadata": {
            "labels": {"re8ch.com/publication-stage": stage},
            "annotations": {
                "toolhive.stacklok.dev/registry-export": exported,
                "re8ch.com/publication-changed-by": "toolhive-platform-admin",
                "re8ch.com/publication-changed-at": changed_at,
            },
        }
    }
    doc = kube_request(
        "PATCH",
        "/apis/toolhive.stacklok.dev/v1beta1/namespaces/toolhive-system/mcpservers/%s" % server,
        patch,
        "application/merge-patch+json",
    )
    metadata = doc.get("metadata", {})
    return {
        "server": server,
        "stage": metadata.get("labels", {}).get("re8ch.com/publication-stage"),
        "registryExport": metadata.get("annotations", {}).get("toolhive.stacklok.dev/registry-export"),
        "changedBy": metadata.get("annotations", {}).get("re8ch.com/publication-changed-by"),
        "changedAt": metadata.get("annotations", {}).get("re8ch.com/publication-changed-at"),
        "rule": "deprecated removes the entry from Registry discovery; canary/internal remain exported",
    }

TENANT_RESOURCE_CATALOG = {
    "qwen": {
        "canonicalTenant": "qwen",
        "displayName": "Qianwen / Qwen",
        "notes": "Historical exception: K3s/Dex namespace and Grafana org are qwen, but Harbor/metrics/Observable tenant label and main databases use qianwen.",
        "identity": {
            "provider": "github",
            "login": "tianruoyou2022",
            "providerID": "99702184",
            "email": "85153954@qq.com",
            "dexSubjects": ["oidc:85153954@qq.com", "oidc:tianruoyou2022"],
        },
        "kubernetes": {
            "namespace": "qwen",
            "dexGroup": "re8ch:qwen-operators",
            "serviceAccount": "qwen-kubectl-operator",
            "tokenSecret": "qwen-kubectl-operator-permanent-token",
        },
        "harbor": {
            "project": "qianwen",
            "webUsername": "tianruoyou2022",
            "membershipRole": "projectAdmin",
            "robotSecret": "harbor-qianwen-admin",
            "pullSecret": "harbor-qianwen-pull",
            "imagePath": "registry.re8ch.com/qianwen/<image>:<tag>",
            "altImagePath": "registry.ynzs.com/qianwen/<image>:<tag>",
        },
        "grafana": {
            "org": "qwen",
            "orgId": "2",
            "dashboardUid": "tenant-qianwen-overview",
            "serviceAccount": "qianwen-ai-agent-viewer",
            "tokenSecret": "grafana-qianwen-viewer",
            "metricsDatasourceUid": "tenant-metrics",
            "logsDatasourceUid": "tenant-vlogs",
            "observableEventsDatasourceUid": "observable-events",
            "tenantLabel": "qianwen",
        },
        "pigsty": {
            "databases": ["qianwen_prod", "qianwen_test", "cardcat"],
            "defaultWriteDatabase": "qianwen_prod",
            "rolePattern": "<db>_{owner,admin,migrator,app_rw,app_ro,pgrst}",
            "recommendedRoutes": PG_SERVICE_ROUTES,
            "credentialSecrets": {
                "qianwen_prod": {
                    "namespace": "qwen",
                    "name": "qianwen-prod-db-credentials",
                    "status": "pending-source",
                    "note": "No trusted server-side password source has been confirmed yet.",
                },
                "qianwen_test": {"namespace": "qwen", "name": "qianwen-test-db-credentials", "status": "complete"},
                "cardcat": {"namespace": "qwen", "name": "cardcat-db-credentials", "status": "complete", "compatibilityCopyOf": "cardcat/cardcat-db-credentials"},
            },
        },
        "cloudbeaver": {
            "url": "https://dbgui.re8ch.com",
            "webUsername": "qwen",
            "team": "tenant-qwen",
            "loginSecret": {"namespace": "qwen", "name": "cloudbeaver-login"},
            "preconfiguredConnections": ["qianwen-prod-write", "qianwen-prod-read-sh", "qianwen-test-write", "cardcat-write"],
            "recommendedDbRoles": {
                "readOnly": "<db>_app_ro",
                "migrations": "<db>_migrator",
                "tenantAdmin": "<db>_admin",
            },
        },
    },
    "ledger": {
        "canonicalTenant": "ledger",
        "displayName": "Ledger",
        "kubernetes": {
            "namespace": "ledger",
            "dexGroup": "re8ch:ledger-operators",
            "serviceAccount": "ledger-kubectl-operator",
            "tokenSecret": "ledger-kubectl-operator-permanent-token",
        },
        "harbor": {
            "project": "ledger",
            "webUsername": "ledger",
            "robotSecret": "harbor-ledger-admin",
            "imagePath": "registry.re8ch.com/ledger/<image>:<tag>",
            "altImagePath": "registry.ynzs.com/ledger/<image>:<tag>",
        },
        "grafana": {
            "org": "Tenant ledger",
            "orgId": "3",
            "dashboardUid": "tenant-ledger-overview",
            "serviceAccount": "ledger-ai-agent-viewer",
            "tokenSecret": "grafana-ledger-viewer",
            "tenantLabel": "ledger",
        },
        "pigsty": {
            "databases": ["ledger"],
            "defaultWriteDatabase": "ledger",
            "rolePattern": "<db>_{owner,admin,migrator,app_rw,app_ro,pgrst}",
            "recommendedRoutes": PG_SERVICE_ROUTES,
            "credentialSecrets": {
                "ledger": {"namespace": "ledger", "name": "ledger-db-credentials", "status": "complete"},
            },
        },
        "cloudbeaver": {
            "url": "https://dbgui.re8ch.com",
            "webUsername": "ledger",
            "team": "tenant-ledger",
            "loginSecret": {"namespace": "ledger", "name": "cloudbeaver-login"},
            "preconfiguredConnections": ["ledger-write", "ledger-read-gz"],
            "recommendedDbRoles": {
                "readOnly": "ledger_app_ro",
                "migrations": "ledger_migrator",
                "tenantAdmin": "ledger_admin",
            },
        },
    },
    "anycam": {
        "canonicalTenant": "anycam",
        "displayName": "Anycam",
        "notes": "Partial tenant. Grafana tenant org is still deferred; K3s, Harbor, CloudBeaver, and PostgreSQL tenant credentials are active.",
        "kubernetes": {
            "namespace": "anycam",
            "dexGroup": "re8ch:anycam-operators",
            "serviceAccount": "anycam-kubectl-operator",
            "tokenSecret": "anycam-kubectl-operator-permanent-token",
        },
        "harbor": {
            "project": "anycam",
            "webUsername": "anycam",
            "robotSecret": "harbor-anycam-admin",
            "pullSecret": "harbor-anycam-pull",
            "imagePath": "registry.re8ch.com/anycam/<image>:<tag>",
            "altImagePath": "registry.ynzs.com/anycam/<image>:<tag>",
        },
        "pigsty": {
            "databases": ["anycam_prod", "anycam_test"],
            "defaultWriteDatabase": "anycam_prod",
            "rolePattern": "<db>_{owner,admin,migrator,app_rw,app_ro,pgrst}",
            "recommendedRoutes": PG_SERVICE_ROUTES,
            "credentialSecrets": {
                "anycam_prod": {"namespace": "anycam", "name": "anycam-prod-db-credentials", "status": "complete"},
                "anycam_test": {"namespace": "anycam", "name": "anycam-test-db-credentials", "status": "complete"},
            },
        },
        "cloudbeaver": {
            "url": "https://dbgui.re8ch.com",
            "webUsername": "anycam",
            "team": "tenant-anycam",
            "notes": "CloudBeaver tenant login can be reconciled after the web account is created.",
            "preconfiguredConnections": ["anycam-prod-write", "anycam-prod-read-gz", "anycam-test-write"],
        },
    },
}


def current_tenant():
    return ACTIVE_TENANT.get()


def current_catalog_key():
    key = current_tenant()
    if key in TENANT_RESOURCE_CATALOG:
        return key
    if TENANT_METRICS_TENANT in TENANT_RESOURCE_CATALOG:
        return TENANT_METRICS_TENANT
    return key


def current_catalog():
    return TENANT_RESOURCE_CATALOG.get(current_catalog_key(), {})


def current_namespace():
    return current_catalog().get("kubernetes", {}).get("namespace", current_tenant())


def current_harbor_project():
    return current_catalog().get("harbor", {}).get("project", TENANT_HARBOR_PROJECT)


def current_harbor_secret():
    return current_catalog().get("harbor", {}).get("robotSecret", TENANT_HARBOR_SECRET)


def current_harbor_robot():
    project = current_harbor_project()
    if project == TENANT_HARBOR_PROJECT:
        return TENANT_HARBOR_ROBOT
    return current_catalog().get("harbor", {}).get("robotUsername", "robot$%s+%s-admin-20260606" % (project, project))


def current_metrics_tenant():
    return current_catalog().get("grafana", {}).get("tenantLabel", current_harbor_project())


def current_namespaces():
    namespace = current_namespace()
    if namespace == TENANT:
        return TENANT_NAMESPACES
    return [namespace]


def current_db_names():
    names = current_catalog().get("pigsty", {}).get("databases", [])
    if names:
        return names
    return TENANT_DB_NAMES


def current_grafana_dashboard():
    grafana = current_catalog().get("grafana", {})
    uid = grafana.get("dashboardUid")
    if uid:
        slug = uid
        return "%s/ui/d/%s/%s" % (GRAFANA_PUBLIC.rstrip("/"), uid, slug)
    if current_tenant() == TENANT:
        return TENANT_GRAFANA_DASHBOARD
    return ""


def current_grafana_token_secret():
    return current_catalog().get("grafana", {}).get("tokenSecret", TENANT_GRAFANA_TOKEN_SECRET)


def current_kube_token_secret():
    kube = current_catalog().get("kubernetes", {})
    return kube.get("tokenSecret", "%s-kubectl-operator-permanent-token" % current_namespace())


def tenant_resource_catalog(all_tenants=False):
    if all_tenants:
        return {"tenants": TENANT_RESOURCE_CATALOG}
    key = current_catalog_key()
    return {"tenant": key, "resources": TENANT_RESOURCE_CATALOG.get(key, {})}


def grafana_access():
    token_secret = current_grafana_token_secret()
    data = get_secret_data(token_secret)
    token = data.get("token")
    if not token:
        raise RuntimeError("secret %s has no data.token" % token_secret)
    url = data.get("url", GRAFANA_PUBLIC).rstrip("/")
    org_id = data.get("orgId", "")
    dashboard = data.get("dashboard", current_grafana_dashboard())
    datasource_uid = data.get("datasourceUid", "tenant-metrics")
    logs_datasource_uid = data.get("logsDatasourceUid", current_catalog().get("grafana", {}).get("logsDatasourceUid", "tenant-vlogs"))
    dashboard_uid = dashboard.rstrip("/").split("/")[-2] if "/d/" in dashboard else "<dashboard-uid>"
    namespace = current_namespace()
    tenant = current_metrics_tenant()
    sample_expr = 'sum(rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m])) or vector(0)' % current_metrics_tenant()
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
    sample_logs_query = 'kubernetes.pod_namespace:%s job:nuclio-pod-logs' % namespace
    return {
        "grafana": url,
        "tenant": tenant,
        "namespace": namespace,
        "orgId": org_id,
        "orgName": data.get("orgName", ""),
        "role": data.get("role", "Viewer"),
        "serviceAccount": data.get("serviceAccount", ""),
        "dashboard": dashboard,
        "datasourceUid": datasource_uid,
        "logsDatasourceUid": logs_datasource_uid,
        "correlationsEnabled": True,
        "tokenType": "Grafana service account token",
        "token": token,
        "headers": {"Authorization": "Bearer <token>"},
        "examples": {
            "dashboard": dashboard,
            "searchDashboards": '%s/ui/api/search?type=dash-db' % url,
            "getDashboard": '%s/ui/api/dashboards/uid/%s' % (url, dashboard_uid),
            "queryDatasource": '%s/ui/api/ds/query' % url,
            "queryLogs": '%s/ui/api/ds/query' % url,
        },
        "grafanaApiHowTo": {
            "step1": "Call get_grafana_access through Ops MCP using the tenant Ops token.",
            "step2": "Read token, grafana, dashboard, datasourceUid, logsDatasourceUid, and examples from this response.",
            "step3": "Send Grafana API requests with Authorization: Bearer <token>.",
            "step4": "Use /ui/api/search?type=dash-db to list tenant-visible dashboards.",
            "step5": "Use /ui/api/dashboards/uid/<uid> to retrieve dashboard JSON and panel PromQL.",
            "step6": "Use /ui/api/ds/query to execute allowed tenant metrics and logs datasource queries.",
            "step7": "In Explore, use correlations from tenant logs fields such as function, pod, and node to jump to tenant-scoped metrics. Tenant dashboard links are intentionally omitted until tenant dashboards are provisioned in the tenant org.",
        },
        "curlExamples": {
            "searchDashboards": 'curl -sS -H "Authorization: Bearer <token>" "%s/ui/api/search?type=dash-db"' % url,
            "getDashboard": 'curl -sS -H "Authorization: Bearer <token>" "%s/ui/api/dashboards/uid/%s"' % (url, dashboard_uid),
            "queryDatasource": 'curl -sS -H "Authorization: Bearer <token>" -H "Content-Type: application/json" "%s/ui/api/ds/query" -d %s' % (url, json.dumps(sample_ds_query, ensure_ascii=False)),
        },
        "sampleDatasourceQuery": sample_ds_query,
        "sampleLogsQuery": {
            "datasourceUid": logs_datasource_uid,
            "logsQL": sample_logs_query,
            "notes": "The tenant-vlogs datasource is backed by the tenant logs proxy. It automatically scopes VictoriaLogs queries to this tenant namespace.",
        },
        "safety": [
            "Use this token for tenant AI agents, not human browser login.",
            "The token is Viewer-scoped to the tenant Grafana org.",
            "The token only sees the tenant org dashboard, tenant-metrics datasource, and tenant-vlogs datasource.",
            "The tenant-metrics datasource is protected by the tenant metrics proxy allowlist.",
            "The tenant-vlogs datasource is protected by the tenant logs proxy namespace scope.",
            "Cross-tenant database, namespace, global label discovery, arbitrary PromQL, and cross-tenant LogsQL queries are rejected.",
            "Do not store this token in source code or prompt history.",
            "For simple metrics summaries, prefer get_tenant_metrics_summary because it does not expose raw Grafana API access.",
        ],
    }


def parse_render_size(value, default, min_value, max_value):
    try:
        parsed = int(str(value))
    except Exception:
        return default
    return max(min(parsed, max_value), min_value)


def safe_time_range_value(value, default):
    value = str(value or default)
    if re.match(r"^(now(-\d+[smhdwMy])?|[0-9]{10,13})$", value):
        return value
    return default


def grafana_render_panel(args):
    args = args or {}
    preset = str(args.get("preset") or "errors").strip().lower()
    if preset not in GRAFANA_RENDER_PRESETS:
        raise ValueError("unsupported grafana render preset: %s" % preset)
    spec = GRAFANA_RENDER_PRESETS[preset]
    access = grafana_access()
    dashboard = str(access.get("dashboard") or current_grafana_dashboard())
    expected_uid = current_catalog().get("grafana", {}).get("dashboardUid", "")
    dashboard_uid = dashboard.rstrip("/").split("/")[-2] if "/d/" in dashboard else expected_uid
    if expected_uid and dashboard_uid != expected_uid:
        raise ValueError("dashboard mismatch")
    if not dashboard_uid:
        raise RuntimeError("grafana dashboard uid is not configured")
    org_id = str(access.get("orgId") or current_catalog().get("grafana", {}).get("orgId") or "")
    slug = dashboard.rstrip("/").split("/")[-1] if "/d/" in dashboard else dashboard_uid
    query = {
        "orgId": org_id,
        "panelId": spec["panelId"],
        "from": safe_time_range_value(args.get("from"), "now-1h"),
        "to": safe_time_range_value(args.get("to"), "now"),
        "width": str(parse_render_size(args.get("width"), spec["width"], 400, 1800)),
        "height": str(parse_render_size(args.get("height"), spec["height"], 240, 1200)),
        "tz": str(args.get("tz") or "Asia/Shanghai"),
        "theme": "light" if str(args.get("theme") or "dark").lower() == "light" else "dark",
    }
    if not query["orgId"]:
        query.pop("orgId", None)
    url = "%s/render/d-solo/%s/%s?%s" % (
        GRAFANA_INTERNAL.rstrip("/"),
        urllib.parse.quote(dashboard_uid, safe=""),
        urllib.parse.quote(slug, safe=""),
        urllib.parse.urlencode(query),
    )
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + access["token"]})
    with urllib.request.urlopen(req, timeout=65) as resp:
        content_type = resp.headers.get("Content-Type", "")
        payload = resp.read(8 * 1024 * 1024)
    if "image/png" not in content_type.lower():
        raise RuntimeError("grafana render returned non-png content type: %s" % content_type)
    return {"preset": preset, "title": spec["title"], "contentType": "image/png", "bytes": payload}


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
    tenant = current_metrics_tenant()
    namespaces = current_namespaces()
    databases_for_tenant = current_db_names()
    ns_re = regex_or_empty(namespaces)
    db_re = regex_or_empty(databases_for_tenant)
    function_count = prom_scalar('count(qianwen_nuclio_function_info{tenant="%s"})' % tenant)
    request_rps = prom_scalar('sum(rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m]))' % tenant)
    error_rps = prom_scalar('sum(rate(qianwen_nuclio_function_errors_total{tenant="%s"}[5m]))' % tenant)
    generation_failure_rps = prom_scalar('sum(rate(qianwen_generation_task_failures_total{tenant="%s"}[5m]))' % tenant)
    p95 = prom_scalar('histogram_quantile(0.95, sum by (le) (rate(qianwen_nuclio_function_request_duration_seconds_bucket{tenant="%s"}[5m])))' % tenant)
    functions = prom_vector('sum by (function) (rate(qianwen_nuclio_function_requests_total{tenant="%s"}[5m]))' % tenant, ["function"])
    errors = prom_vector('sum by (function) (rate(qianwen_nuclio_function_errors_total{tenant="%s"}[5m]))' % tenant, ["function"])
    generation_failures = prom_vector('sum by (task_type,provider,error_code,error_phase,error_origin,severity) (rate(qianwen_generation_task_failures_total{tenant="%s"}[5m]))' % tenant, ["task_type", "provider", "error_code", "error_phase", "error_origin", "severity"])
    running_pods = prom_vector('sum by (namespace) (kube_pod_status_phase{namespace=~"%s",phase="Running"})' % ns_re, ["namespace"])
    restarts = prom_vector('sum by (namespace,pod) (kube_pod_container_status_restarts_total{namespace=~"%s"})' % ns_re, ["namespace", "pod"])
    databases = []
    if databases_for_tenant:
        backends = {x["datname"]: x["value"] for x in prom_vector('pg:db:num_backends{datname=~"%s"}' % db_re, ["datname"])}
        xact = {x["datname"]: x["value"] for x in prom_vector('pg:db:xact_total_rate5m{datname=~"%s"}' % db_re, ["datname"])}
        size = {x["datname"]: x["value"] for x in prom_vector('pg:db:db_size{datname=~"%s"}' % db_re, ["datname"])}
        conn = {x["datname"]: x["value"] for x in prom_vector('pg:db:conn_usage{datname=~"%s"}' % db_re, ["datname"])}
        for name in databases_for_tenant:
            databases.append({
                "datname": name,
                "backends": backends.get(name, 0.0),
                "xactRate5m": xact.get(name, 0.0),
                "sizeBytes": size.get(name, 0.0),
                "connUsage": conn.get(name, 0.0),
            })
    return {
        "tenant": tenant,
        "namespace": current_namespace(),
        "namespaces": namespaces,
        "databases": databases_for_tenant,
        "metricsSource": METRICS_URL,
        "grafanaDashboard": current_grafana_dashboard(),
        "summary": {
            "functionCount": function_count,
            "requestRps5m": request_rps,
            "errorRps5m": error_rps,
            "generationTaskFailureRps5m": generation_failure_rps,
            "functionP95Seconds5m": p95,
        },
        "functionsByRps": sorted(functions, key=lambda x: x["value"], reverse=True)[:20],
        "errorsByRps": sorted(errors, key=lambda x: x["value"], reverse=True)[:20],
        "generationFailuresByCode5m": sorted(generation_failures, key=lambda x: x["value"], reverse=True)[:20],
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
    ops_mcp_rb = "ops-mcp-%s-operator" % namespace
    ensure_rolebinding(namespace, human_rb, "tenant-operator", [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": rbac_group_name(group)}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "human-oidc-group"}, annotations={"re8ch.com/pairs-with-serviceaccount": "%s/%s" % (namespace, serviceaccount)})
    ensure_rolebinding(namespace, robot_rb, "tenant-operator", [{"kind": "ServiceAccount", "name": serviceaccount, "namespace": namespace}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "robot-serviceaccount"}, annotations={"re8ch.com/pairs-with-oidc-group": rbac_group_name(group)})
    ensure_rolebinding(namespace, ops_mcp_rb, "tenant-operator", [{"kind": "ServiceAccount", "name": "ops-mcp-server", "namespace": "ops"}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "operator", "re8ch.com/principal-type": "ops-mcp-serviceaccount"}, annotations={"re8ch.com/reason": "Ops MCP uses this binding for tenant-scoped list/restart helpers; caller tenant token still selects the namespace."})
    ensure_clusterrole("tenant-ui-cluster-viewer", tenant_ui_cluster_viewer_rules())
    human_crb = "%s-headlamp-ui-cluster-viewer-oidc" % namespace
    robot_crb = "%s-kubectl-operator-ui-cluster-viewer" % namespace
    ensure_clusterrolebinding(human_crb, "tenant-ui-cluster-viewer", [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": rbac_group_name(group)}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "cluster-readonly", "re8ch.com/principal-type": "human-oidc-group"})
    ensure_clusterrolebinding(robot_crb, "tenant-ui-cluster-viewer", [{"kind": "ServiceAccount", "name": serviceaccount, "namespace": namespace}], labels={"re8ch.com/tenant": namespace, "re8ch.com/access-level": "cluster-readonly", "re8ch.com/principal-type": "robot-serviceaccount"})
    return {"namespace": namespace, "dexGroup": group, "rbacGroup": rbac_group_name(group), "serviceAccount": "%s/%s" % (namespace, serviceaccount), "tokenSecret": "%s/%s" % (namespace, token_secret), "role": "%s/tenant-operator" % namespace, "humanRoleBinding": "%s/%s" % (namespace, human_rb), "robotRoleBinding": "%s/%s" % (namespace, robot_rb), "opsMcpRoleBinding": "%s/%s" % (namespace, ops_mcp_rb), "clusterRole": "tenant-ui-cluster-viewer", "humanClusterRoleBinding": human_crb, "robotClusterRoleBinding": robot_crb}



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


def authentik_request(method, path, body=None):
    if not AUTHENTIK_API_TOKEN:
        raise RuntimeError("Authentik admin API token is not configured")
    url = "%s/%s" % (AUTHENTIK_API_URL, path.lstrip("/"))
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("Authorization", "Bearer %s" % AUTHENTIK_API_TOKEN)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
            return json.loads(data.decode("utf-8")) if data else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError("Authentik API %s %s failed (%s): %s" % (method, path, exc.code, detail))


def authentik_group(name):
    query = urllib.parse.urlencode({"name": name, "page_size": 100})
    results = authentik_request("GET", "/core/groups/?%s" % query).get("results", [])
    return next((item for item in results if item.get("name") == name), None)


def authentik_user(username=None, email=None):
    query = urllib.parse.urlencode({"search": username or email or "", "page_size": 100})
    results = authentik_request("GET", "/core/users/?%s" % query).get("results", [])
    for item in results:
        if username and item.get("username") == username:
            return item
        if email and str(item.get("email") or "").lower() == email.lower():
            return item
    return None


def list_authentik_users():
    results = authentik_request("GET", "/core/users/?page_size=100").get("results", [])
    users = []
    for item in results:
        users.append({
            "pk": item.get("pk"),
            "username": item.get("username"),
            "name": item.get("name"),
            "email": item.get("email"),
            "isActive": item.get("is_active"),
            "groups": sorted(group.get("name") for group in item.get("groups_obj", []) if group.get("name")),
            "path": item.get("path"),
        })
    return {"count": len(users), "users": users, "credentialsReturned": False}


def ensure_authentik_user(args):
    args = args or {}
    username = str(args.get("username") or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{1,149}$", username):
        raise ValueError("username must be 2-150 audit-safe characters")
    email = validate_email(args.get("email")) if args.get("email") else ""
    group_names = sorted(set(str(value).strip() for value in (args.get("groups") or []) if str(value).strip()))
    groups = []
    for name in group_names:
        group = authentik_group(name)
        if not group:
            raise ValueError("Authentik group does not exist: %s" % name)
        groups.append(group.get("pk"))
    payload = {
        "username": username,
        "name": str(args.get("name") or username).strip(),
        "email": email,
        "is_active": bool(args.get("isActive", True)),
        "groups": groups,
        "path": str(args.get("path") or "users"),
        "type": "internal",
    }
    existing = authentik_user(username=username)
    if existing:
        result = authentik_request("PATCH", "/core/users/%s/" % existing.get("pk"), payload)
        action = "updated"
    else:
        result = authentik_request("POST", "/core/users/", payload)
        action = "created"
    return {
        "action": action,
        "user": {key: result.get(key) for key in ("pk", "username", "name", "email", "is_active", "path")},
        "groups": group_names,
        "credentialsReturned": False,
        "nextStep": "Use the Authentik recovery flow or administrator UI to establish credentials; this tool never accepts or returns passwords.",
    }


def grant_authentik_user_group(args):
    args = args or {}
    user = authentik_user(username=args.get("username"), email=args.get("email"))
    if not user:
        raise ValueError("Authentik user was not found")
    group_name = str(args.get("group") or "").strip()
    group = authentik_group(group_name)
    if not group:
        raise ValueError("Authentik group does not exist: %s" % group_name)
    authentik_request("POST", "/core/groups/%s/add_user/" % group.get("pk"), {"pk": user.get("pk")})
    return {
        "action": "granted",
        "username": user.get("username"),
        "email": user.get("email"),
        "group": group_name,
        "credentialsReturned": False,
    }


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
        "purpose": "Standardize Headlamp/Dex human access, kubectl robot access, Harbor registry identity, and optional Pigsty PostgreSQL database planning for a tenant namespace.",
        "auth": "Use Ops MCP admin token. Normal Ops token cannot call these tools.",
        "principle": "Human OIDC group, Dex local OIDC user, and robot ServiceAccount are reconciled to tenant operator permissions, plus the same read-only cluster UI view.",
        "steps": [
            {"step": 1, "tool": "get_dex_hash_instructions", "when": "Before creating a new Dex static user.", "result": "Generate passwordHash with htpasswd/docker; do not send plaintext password to Ops MCP."},
            {"step": 2, "tool": "ensure_tenant_access", "when": "Create or repair namespace-level access without changing Dex users.", "arguments": {"namespace": "ledger"}, "result": "Ensures namespace, tenant-operator/viewer roles, OIDC group RoleBinding, robot ServiceAccount token, and cluster read-only bindings."},
            {"step": 3, "tool": "create_dex_user", "when": "Create a new human login and grant namespace access in one call.", "arguments": {"email": "alice@example.com", "username": "alice", "userID": "alice", "passwordHash": "$2a$10$...", "namespace": "ledger"}, "result": "Adds static Dex user to re8ch:ledger-operators, binds oidc:<email> directly for Dex local login, reconciles tenant RBAC, and restarts Dex."},
            {"step": 4, "tool": "grant_user_namespace", "when": "Existing Dex user needs access to another namespace.", "arguments": {"email": "alice@example.com", "namespace": "ledger"}, "result": "Adds re8ch:ledger-operators to existing Dex user, binds oidc:<email> directly for Dex local login, reconciles tenant RBAC, and restarts Dex."},
            {"step": 5, "tool": "ensure_harbor_tenant", "when": "Create or repair the matching Harbor project, web login, project admin membership, robot token secret, and imagePullSecrets.", "arguments": {"namespace": "ledger", "email": "ledger@re8ch.com"}, "result": "Ensures Harbor project ledger, Harbor username ledger, K8s secret ledger/harbor-ledger-admin, and registry image path registry.re8ch.com/ledger/<image>:<tag>."},
            {"step": 6, "tool": "generate_pg_tenant_database_plan", "when": "The tenant also needs a Pigsty PostgreSQL database and standard roles.", "arguments": {"database": "ledger", "tenant": "ledger", "schema": "app", "generatePasswords": True}, "result": "Returns SQL, psql execution hints, role names, connection routes, and secret storage paths. It does not touch PostgreSQL by itself."},
            {"step": 7, "verify": ["kubectl auth can-i --as=oidc:<email> --as-group=oidc:re8ch:<namespace>-operators -n <namespace> create deployments.apps", "kubectl auth can-i --as=system:serviceaccount:<namespace>:<namespace>-kubectl-operator -n <namespace> create deployments.apps", "kubectl auth can-i --as=oidc:<email> --as-group=oidc:re8ch:<namespace>-operators -n other-namespace get pods should be no", "kubectl -n <namespace> get secret harbor-<namespace>-admin", "PGPASSWORD='<password>' psql -h pgwrite.zt.re8ch.com -U <database>_app_rw -d <database> -c 'select current_database(), current_user'"]},
        ],
        "createdObjects": ["namespace/<namespace>", "role/<namespace>/tenant-operator", "role/<namespace>/tenant-viewer", "rolebinding/<namespace>/<namespace>-operators-tenant-operator-oidc", "serviceaccount/<namespace>/<namespace>-kubectl-operator", "secret/<namespace>/<namespace>-kubectl-operator-permanent-token", "rolebinding/<namespace>/<namespace>-kubectl-operator", "clusterrole/tenant-ui-cluster-viewer", "clusterrolebinding/<namespace>-headlamp-ui-cluster-viewer-oidc", "clusterrolebinding/<namespace>-kubectl-operator-ui-cluster-viewer", "harbor project/<namespace>", "harbor user/<namespace>", "secret/<namespace>/harbor-<namespace>-admin"],
        "safety": ["The workflow is idempotent and uses apply-like create/update behavior.", "It does not delete existing namespace workloads or business secrets.", "Dex user creation requires passwordHash, not plaintext password.", "Harbor robot tokens are stored in Kubernetes secrets and are not returned by the API.", "Pigsty database SQL must be executed by a DBA or by a separately authorized database automation; the public Ops MCP server does not hold PostgreSQL superuser credentials.", "Dex changes restart the Dex deployment; existing users may need to log in again to receive new groups."],
    }


def get_authentik_tenancy_admin_workflow():
    return {
        "purpose": "Provision Authentik human identity, Kubernetes namespace RBAC, Harbor access, and optional Pigsty database resources without exposing credentials.",
        "identityAuthority": "Authentik",
        "legacyIdentity": "Dex tools remain available only on the legacy Ops MCP during migration and are not published by re8ch-platform-admin.",
        "steps": [
            {"step": 1, "tool": "ensure_authentik_user", "result": "Create or update the user and reviewed group memberships without accepting a password."},
            {"step": 2, "tool": "grant_authentik_user_group", "result": "Grant Platform Operators, Platform Admins, or a reviewed tenant group."},
            {"step": 3, "tool": "ensure_tenant_access", "result": "Create or repair namespace, ServiceAccount, Role and RoleBinding resources."},
            {"step": 4, "tool": "ensure_harbor_tenant", "result": "Create or repair Harbor project membership and store robot material only in Kubernetes Secrets."},
            {"step": 5, "tool": "generate_pg_tenant_database_plan", "result": "Generate the reviewed database/role plan; SQL execution remains a separate DBA authority."},
            {"step": 6, "tool": "ensure_tenant_database_secrets", "result": "Create the standard Kubernetes Secret and return only its reference."},
        ],
        "safety": [
            "No tool returns Authentik passwords, sessions, OAuth tokens, Harbor robot tokens, or Kubernetes Secret values.",
            "Explicitly requested admin work may proceed without repeating confirmation; each call remains allowlisted, audited and subject to native Authentik/Kubernetes authorization.",
            "Authentik group policy and Kubernetes RBAC remain authoritative even if a client omits local approval UX.",
        ],
    }


def pg_ident(name):
    if not name or not re.match(r"^[a-z][a-z0-9_]{0,62}$", name):
        raise ValueError("PostgreSQL identifiers must match ^[a-z][a-z0-9_]{0,62}$: %s" % name)
    return name


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def tenant_pg_passwords(database, generate=False, provided=None):
    provided = provided or {}
    roles = ["admin", "migrator", "app_rw", "app_ro", "pgrst"]
    out = {}
    for suffix in roles:
        key = "%s_%s" % (database, suffix)
        if suffix in provided and provided[suffix]:
            out[suffix] = provided[suffix]
        elif key in provided and provided[key]:
            out[suffix] = provided[key]
        elif generate:
            out[suffix] = secrets.token_urlsafe(36)
        else:
            out[suffix] = "<%s_PASSWORD>" % key.upper()
    return out


def postgres_url(user, password, host, port, database):
    return "postgresql://%s:%s@%s:%s/%s" % (
        urllib.parse.quote(user, safe=""),
        urllib.parse.quote(password, safe=""),
        host,
        port,
        database,
    )


def build_database_secret_values(database, passwords, read_host=None):
    read_host = read_host or "pgread-gz.zt.re8ch.com"
    prefix = database_env_prefix(database)
    values = {
        "PGDATABASE": database,
        "PGPORT": "5432",
        "PGWRITE_PORT": "5433",
        "PGREAD_PORT": "5434",
        "PGPOOL_PORT": "6432",
        "PGHOST_WRITE": "pgwrite.zt.re8ch.com",
        "PGHOST_READ_SH": "pgread-sh.zt.re8ch.com",
        "PGHOST_READ_GZ": "pgread-gz.zt.re8ch.com",
        "PGHOST_POOL": "pgwrite.zt.re8ch.com",
        "PGROUTE_NOTE": "Recommended direct write/admin/migration route is pgwrite.zt.re8ch.com:5433; 5432 is a PostgreSQL instance/JDBC GUI port, not the default HA write route.",
    }
    for suffix, password in passwords.items():
        if not password or str(password).startswith("<"):
            continue
        role_key = suffix.upper()
        values["%s_%s_USER" % (prefix, role_key)] = "%s_%s" % (database, suffix)
        values["%s_%s_PASSWORD" % (prefix, role_key)] = password
    if passwords.get("app_rw") and not str(passwords.get("app_rw")).startswith("<"):
        values["DATABASE_URL_RW"] = postgres_url("%s_app_rw" % database, passwords["app_rw"], "pgwrite.zt.re8ch.com", "5433", database)
        values["DATABASE_URL_POOL_RW"] = postgres_url("%s_app_rw" % database, passwords["app_rw"], "pgwrite.zt.re8ch.com", "6432", database)
    if passwords.get("app_ro") and not str(passwords.get("app_ro")).startswith("<"):
        values["DATABASE_URL_RO"] = postgres_url("%s_app_ro" % database, passwords["app_ro"], read_host, "5434", database)
    if passwords.get("migrator") and not str(passwords.get("migrator")).startswith("<"):
        values["DATABASE_URL_MIGRATOR"] = postgres_url("%s_migrator" % database, passwords["migrator"], "pgwrite.zt.re8ch.com", "5433", database)
    if passwords.get("pgrst") and not str(passwords.get("pgrst")).startswith("<"):
        values["PGRST_DB_URI"] = postgres_url("%s_pgrst" % database, passwords["pgrst"], "pgwrite.zt.re8ch.com", "5433", database)
    return values


def upsert_database_credentials_secret(namespace, database, passwords, tenant=None, read_host=None):
    namespace = validate_k8s_name(namespace, "namespace")
    database = pg_ident(database)
    tenant = tenant or namespace
    values = build_database_secret_values(database, passwords, read_host=read_host)
    present_roles = sorted([k for k, v in passwords.items() if v and not str(v).startswith("<")])
    obj = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": secret_name_for_database(database),
            "namespace": namespace,
            "labels": {
                "re8ch.com/tenant": namespace,
                "re8ch.com/secret-purpose": "postgres-credentials",
                "re8ch.com/database": database,
            },
            "annotations": {
                "re8ch.com/source": "ops-mcp",
                "re8ch.com/credential-status": "complete" if {"migrator", "app_rw", "app_ro", "pgrst"}.issubset(set(present_roles)) else "partial",
                "re8ch.com/roles": ",".join(present_roles),
                "re8ch.com/tenant-catalog-key": tenant,
            },
        },
        "data": {k: b64encode_text(str(v)) for k, v in values.items()},
    }
    base = "/api/v1/namespaces/%s/secrets" % namespace
    kube_apply(base, base + "/" + secret_name_for_database(database), obj)
    return {"namespace": namespace, "name": secret_name_for_database(database), "database": database, "storedKeys": sorted(values.keys()), "roles": present_roles}


def ensure_tenant_database_secrets(args):
    args = args or {}
    namespace = validate_k8s_name(args.get("namespace") or args.get("tenant") or "", "namespace")
    database = pg_ident(args.get("database") or args.get("db") or "")
    tenant = args.get("tenant") or namespace
    generate_passwords = bool(args.get("generatePasswords", False))
    passwords = tenant_pg_passwords(database, generate=generate_passwords, provided=args.get("passwords") or {})
    if not generate_passwords and not args.get("passwords"):
        raise ValueError("provide passwords or set generatePasswords=true")
    read_host = args.get("readHost") or "pgread-gz.zt.re8ch.com"
    return upsert_database_credentials_secret(namespace, database, passwords, tenant=tenant, read_host=read_host)


def ensure_tenant_database_bundle(args):
    args = args or {}
    namespace = validate_k8s_name(args.get("namespace") or args.get("tenant") or "", "namespace")
    database = pg_ident(args.get("database") or args.get("db") or "")
    tenant = args.get("tenant") or namespace
    schema = args.get("schema") or "app"
    generate_passwords = bool(args.get("generatePasswords", False))
    passwords = tenant_pg_passwords(database, generate=generate_passwords, provided=args.get("passwords") or {})
    pg_plan = generate_pg_tenant_database_plan({
        "database": database,
        "tenant": tenant,
        "schema": schema,
        "grantCreateRole": bool(args.get("grantCreateRole", False)),
        "passwords": passwords,
    })
    secret = None
    if generate_passwords or args.get("passwords"):
        secret = upsert_database_credentials_secret(namespace, database, passwords, tenant=tenant, read_host=args.get("readHost") or "pgread-gz.zt.re8ch.com")
    return {
        "tenant": tenant,
        "namespace": namespace,
        "database": database,
        "databaseSecret": secret or {"namespace": namespace, "name": secret_name_for_database(database), "status": "not-created; provide passwords or generatePasswords=true"},
        "pigstyPlan": pg_plan,
        "cloudbeaver": {
            "webLoginSecret": "%s/cloudbeaver-login" % namespace,
            "expectedTeam": "tenant-%s" % namespace,
            "expectedConnectionIds": ["%s-write" % database.replace("_", "-"), "%s-read-gz" % database.replace("_", "-")],
            "note": "CloudBeaver connection/user reconciliation should use the same tenant Secret model; existing CloudBeaver API state is not stored in Ops MCP.",
        },
    }


def generate_pg_tenant_database_plan(args):
    database = pg_ident((args or {}).get("database") or (args or {}).get("db") or "")
    tenant = pg_ident((args or {}).get("tenant") or database)
    schema = pg_ident((args or {}).get("schema") or "app")
    generate_passwords = bool((args or {}).get("generatePasswords", False))
    grant_create_role = bool((args or {}).get("grantCreateRole", False))
    passwords = tenant_pg_passwords(database, generate=generate_passwords, provided=(args or {}).get("passwords") or {})

    roles = {
        "owner": "%s_owner" % database,
        "admin": "%s_admin" % database,
        "migrator": "%s_migrator" % database,
        "app_rw": "%s_app_rw" % database,
        "app_ro": "%s_app_ro" % database,
        "pgrst": "%s_pgrst" % database,
    }
    for value in roles.values():
        pg_ident(value)

    connect_roles = ", ".join([roles["admin"], roles["migrator"], roles["app_rw"], roles["app_ro"], roles["pgrst"]])
    dml_roles = ", ".join([roles["app_rw"], roles["pgrst"]])
    all_default_owners = [roles["owner"], roles["admin"], roles["migrator"]]

    sql = [
        "-- Run on the Pigsty PostgreSQL write leader as a DBA/superuser.",
        "-- Preferred HA write/admin entry: pgwrite.zt.re8ch.com:5433.",
        "-- Avoid using PostgreSQL instance port 5432 as the default write route unless intentionally targeting one instance.",
        "CREATE ROLE %s NOLOGIN;" % roles["owner"],
        "CREATE ROLE %s LOGIN PASSWORD %s;" % (roles["admin"], sql_literal(passwords["admin"])),
        "CREATE ROLE %s LOGIN PASSWORD %s;" % (roles["migrator"], sql_literal(passwords["migrator"])),
        "CREATE ROLE %s LOGIN PASSWORD %s;" % (roles["app_rw"], sql_literal(passwords["app_rw"])),
        "CREATE ROLE %s LOGIN PASSWORD %s;" % (roles["app_ro"], sql_literal(passwords["app_ro"])),
        "CREATE ROLE %s LOGIN PASSWORD %s;" % (roles["pgrst"], sql_literal(passwords["pgrst"])),
    ]
    if grant_create_role:
        sql.append("-- WARNING: CREATEROLE is cluster-wide. Grant only to trusted tenant admins.")
        sql.append("ALTER ROLE %s CREATEROLE;" % roles["admin"])
    sql += [
        "CREATE DATABASE %s OWNER %s TEMPLATE template0 ENCODING 'UTF8';" % (database, roles["owner"]),
        "REVOKE ALL ON DATABASE %s FROM PUBLIC;" % database,
        "GRANT CONNECT, TEMPORARY ON DATABASE %s TO %s;" % (database, connect_roles),
        "GRANT CREATE ON DATABASE %s TO %s, %s;" % (database, roles["admin"], roles["migrator"]),
        "\\connect %s" % database,
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
        "CREATE SCHEMA IF NOT EXISTS %s AUTHORIZATION %s;" % (schema, roles["owner"]),
        "GRANT USAGE ON SCHEMA %s TO %s, %s, %s, %s;" % (schema, roles["admin"], roles["migrator"], roles["app_ro"], roles["app_rw"]),
        "GRANT USAGE ON SCHEMA %s TO %s;" % (schema, roles["pgrst"]),
        "GRANT CREATE ON SCHEMA %s TO %s, %s;" % (schema, roles["admin"], roles["migrator"]),
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %s TO %s;" % (schema, dml_roles),
        "GRANT SELECT ON ALL TABLES IN SCHEMA %s TO %s;" % (schema, roles["app_ro"]),
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %s TO %s;" % (schema, dml_roles),
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %s TO %s;" % (schema, roles["app_ro"]),
    ]
    for owner_role in all_default_owners:
        sql += [
            "ALTER DEFAULT PRIVILEGES FOR ROLE %s IN SCHEMA %s GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %s;" % (owner_role, schema, dml_roles),
            "ALTER DEFAULT PRIVILEGES FOR ROLE %s IN SCHEMA %s GRANT SELECT ON TABLES TO %s;" % (owner_role, schema, roles["app_ro"]),
            "ALTER DEFAULT PRIVILEGES FOR ROLE %s IN SCHEMA %s GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %s;" % (owner_role, schema, dml_roles),
            "ALTER DEFAULT PRIVILEGES FOR ROLE %s IN SCHEMA %s GRANT USAGE, SELECT ON SEQUENCES TO %s;" % (owner_role, schema, roles["app_ro"]),
        ]
    sql += [
        "ALTER ROLE %s IN DATABASE %s SET search_path = %s, public;" % (roles["admin"], database, schema),
        "ALTER ROLE %s IN DATABASE %s SET search_path = %s, public;" % (roles["migrator"], database, schema),
        "ALTER ROLE %s IN DATABASE %s SET search_path = %s, public;" % (roles["app_rw"], database, schema),
        "ALTER ROLE %s IN DATABASE %s SET search_path = %s, public;" % (roles["app_ro"], database, schema),
        "ALTER ROLE %s IN DATABASE %s SET search_path = %s, public;" % (roles["pgrst"], database, schema),
    ]

    secret_paths = {
        suffix: "/home/ubuntu/.pgbiz-secrets/%s_%s.password" % (database, suffix)
        for suffix in ["admin", "migrator", "app_rw", "app_ro", "pgrst"]
    }
    return {
        "tenant": tenant,
        "database": database,
        "schema": schema,
        "rolePattern": roles,
        "passwords": passwords if generate_passwords or (args or {}).get("passwords") else "not returned; pass generatePasswords=true for one-time generation or provide passwords explicitly",
        "secretPathsOnPrimary": secret_paths,
        "k8sCredentialSecret": {
            "namespace": tenant,
            "name": secret_name_for_database(database),
            "purpose": "postgres-credentials",
            "note": "K3s Secret is the credential source of truth after SQL execution.",
        },
        "connectionRoutes": {
            "recommendedWrite": "pgwrite.zt.re8ch.com:5433",
            "recommendedRead": "pgread-sh.zt.re8ch.com:5434 or pgread-gz.zt.re8ch.com:5434",
            "pool": "pgwrite.zt.re8ch.com:6432",
            "instancePort": "pgwrite.zt.re8ch.com:5432; direct instance/JDBC GUI path, not the default HA write route",
            "postgrestGateway": "http://192.0.2.89:3080, http://192.0.2.112:3080, http://192.0.2.251:3080",
            "details": PG_SERVICE_ROUTES,
        },
        "recommendedWebAccess": {
            "tenantReadOnly": "%s_app_ro" % database,
            "migrationOperator": "%s_migrator" % database,
            "adminOperator": "%s_admin" % database,
            "tools": ["DBeaver desktop", "CloudBeaver if a shared web SQL console is required", "Bytebase if migration review/audit is required", "pgAdmin only as a simple optional GUI"],
            "note": "CloudBeaver/DBeaver may use PostgreSQL JDBC against 5432 for GUI inspection, but automation and migrations should prefer the HA write service on 5433 or PgBouncer on 6432.",
        },
        "psqlCommand": "psql -h pgwrite.zt.re8ch.com -p 5433 -U <pigsty_dba_or_postgres> -d postgres -v ON_ERROR_STOP=1 -f <generated-sql-file>",
        "sql": "\n".join(sql) + "\n",
        "safety": [
            "This tool generates a database plan and SQL; it does not execute SQL and does not store PostgreSQL superuser credentials.",
            "Do not grant CREATEROLE by default. It is cluster-wide, not database-local.",
            "app_rw is for runtime DML and should not create tenant roles.",
            "migrator is for migrations and DDL within the tenant database.",
            "pgrst is for PostgREST integration.",
            "owner is NOLOGIN and should own database/schema objects.",
        ],
    }

OBSERVE_TOOL_NAMES = {
    "get_usage_guide", "get_tenant_context", "list_k8s_resources", "get_registry_info",
    "get_tenant_resource_catalog", "get_grafana_links", "get_tenant_metrics_summary",
    "get_platform_service_health", "get_platform_service_catalog", "get_platform_permission_coverage",
    "get_platform_access_posture", "get_nuclio_function_status", "diagnose_nuclio_function",
    "get_nuclio_capacity_status", "get_litellm_gateway_status", "get_fleet_control_status",
    "get_supabase_platform_status", "get_neo4j_platform_status", "get_identity_platform_status",
    "get_agent_runtime_status", "get_observability_platform_status", "get_artifact_platform_status",
    "get_database_routing_status", "get_external_publication_readiness",
}
OPERATE_TOOL_NAMES = {
    "get_managed_platform_operation_status", "restart_managed_platform_deployment",
    "scale_managed_platform_deployment",
}
ADMIN_TOOL_NAMES = {
    "list_dex_users", "get_dex_hash_instructions", "create_dex_user", "grant_user_namespace",
    "get_tenancy_admin_workflow", "generate_pg_tenant_database_plan",
    "ensure_tenant_database_secrets", "ensure_tenant_database_bundle", "ensure_tenant_access",
    "ensure_harbor_tenant", "get_tenant_secret_refs", "set_tool_publication_stage",
}


def mcp_tools(include_admin=False):
    def schema(props=None, required=None):
        return {"type": "object", "properties": props or {}, "required": required or []}
    tools = [
        {"name": "get_public_tool_service_info", "description": "Return the public RE8CH ToolHive service contract without internal topology, tenant data, or credentials.", "inputSchema": schema()},
        {"name": "get_usage_guide", "description": "Return the Ops usage guide.", "inputSchema": schema()},
        {"name": "get_tenant_context", "description": "Return the authenticated tenant endpoints and safety boundaries.", "inputSchema": schema()},
        {"name": "get_kubeconfig", "description": "Return the authenticated tenant operator kubeconfig for CLI automation.", "inputSchema": schema()},
        {"name": "list_k8s_resources", "description": "List tenant Kubernetes resources or read-only nodes.", "inputSchema": schema({"kind": {"type": "string", "default": "pods"}})},
        {"name": "restart_deployment", "description": "Restart one Deployment in the authenticated tenant namespace.", "inputSchema": schema({"deployment": {"type": "string"}}, ["deployment"])},
        {"name": "get_registry_info", "description": "Return Harbor registry information for authenticated tenant images.", "inputSchema": schema()},
        {"name": "get_tenant_resource_catalog", "description": "Return the tenant resource name catalog. Normal token returns this tenant; admin token can pass allTenants=true.", "inputSchema": schema({"allTenants": {"type": "boolean", "default": False}})},
        {"name": "get_grafana_links", "description": "Return Grafana links and intended usage.", "inputSchema": schema()},
        {"name": "get_grafana_access", "description": "Return the tenant Grafana service account token and API usage hints for AI agents. The token is Viewer-scoped to the tenant Grafana org.", "inputSchema": schema()},
        {"name": "get_tenant_metrics_summary", "description": "Return tenant-scoped function, namespace, and database metrics. The tenant filters are enforced by Ops MCP configuration; arbitrary PromQL is not accepted.", "inputSchema": schema()},
        {"name": "get_platform_service_health", "description": "Probe fixed, reviewed platform endpoints across identity, model, Agent, artifact, database, application, device, observability and ToolHive domains. Arbitrary targets and credentials are not accepted.", "inputSchema": schema()},
        {"name": "get_platform_service_catalog", "description": "Return a live, credential-free inventory of platform namespaces, workloads, Services, Ingress hosts, control/data-plane classification, default Agent permissions, and human tracking GUIs.", "inputSchema": schema()},
        {"name": "get_platform_permission_coverage", "description": "Return the effective observe/operate/admin, native-authority and human-GUI decision for every live workload and Service, generated from live inventory and exact ToolHive operation allowlists.", "inputSchema": schema()},
        {"name": "get_platform_access_posture", "description": "Return credential-free namespace RBAC, ServiceAccount token, NetworkPolicy, NodePort/LoadBalancer, and Ingress review signals for AI and human security review.", "inputSchema": schema()},
        {"name": "get_managed_platform_operation_status", "description": "Return desired/ready/available state and recorded restart/scale approvals for every pre-reviewed platform operation target.", "inputSchema": schema()},
        {"name": "get_nuclio_function_status", "description": "Return a sanitized cross-namespace Nuclio function inventory and native states without environment variables, Secret references, annotations or last-applied configuration.", "inputSchema": schema()},
        {"name": "diagnose_nuclio_function", "description": "Diagnose one existing Nuclio function from sanitized native state, Deployment readiness, Pod scheduling/container waits and recent Events. Secret, environment and annotation data are omitted.", "inputSchema": schema({"namespace": {"type": "string"}, "name": {"type": "string"}}, ["namespace", "name"])},
        {"name": "get_nuclio_capacity_status", "description": "Return Nuclio-labelled node readiness, schedulability, taints, allocatable capacity, placed function Pod counts and unscheduled function Pods as a rollout gate.", "inputSchema": schema()},
        {"name": "get_litellm_gateway_status", "description": "Return LiteLLM public liveness/readiness and Deployment availability without reading or proxying master keys, provider keys, model configuration, teams or budgets.", "inputSchema": schema()},
        {"name": "get_fleet_control_status", "description": "Return Fleet public health/version and Fleet/egress/Redis/MySQL workload readiness without reading device inventory, policies, queries, activities, campaigns or credentials.", "inputSchema": schema()},
        {"name": "get_supabase_platform_status", "description": "Return Supabase Studio reachability plus component Deployment and endpoint readiness without reading JWT keys, credentials, users, rows, buckets, objects or schema contents.", "inputSchema": schema()},
        {"name": "get_neo4j_platform_status", "description": "Return Neo4j Browser reachability plus StatefulSet and endpoint readiness without reading graph credentials, Cypher, graph data, schema or configuration.", "inputSchema": schema()},
        {"name": "get_identity_platform_status", "description": "Return Authentik and application auth-gateway reachability plus Deployment and endpoint readiness without reading identities, sessions, tokens, secrets, claims or policy contents.", "inputSchema": schema()},
        {"name": "get_agent_runtime_status", "description": "Return sanitized OpenSandbox and Codex workload readiness, RemoteMCP acceptance/tool counts and aggregate sandbox resource phases without reading prompts, sessions, credentials, files, commands or output.", "inputSchema": schema()},
        {"name": "get_observability_platform_status", "description": "Return fixed Grafana/VictoriaMetrics/VMAlert/VictoriaLogs/OpenCost/Robusta/OTLP health, workload readiness, aggregate target counts and one-hour cluster cost/efficiency without arbitrary queries or tenant detail.", "inputSchema": schema()},
        {"name": "get_artifact_platform_status", "description": "Return Harbor core component and workload readiness plus the reviewed publication flow without reading credentials, artifacts, vulnerability details, users or robot data.", "inputSchema": schema()},
        {"name": "get_database_routing_status", "description": "Return Pigsty PostgreSQL/PgBouncer/PostgREST route reachability, sanitized EndpointSlice readiness and CloudBeaver reachability without SQL, schema, row, credential or connection data.", "inputSchema": schema()},
        {"name": "get_external_publication_readiness", "description": "Return fixed tools/auth DNS, trusted TLS, OAuth metadata, MCP challenge and cluster publication-object gates without reading OAuth secrets, tokens, certificate bodies, DNS credentials or user claims.", "inputSchema": schema()},
        {"name": "get_tenant_secret_refs", "description": "Return tenant-readable Kubernetes Secret references. Normal token is limited to this tenant namespace; admin can pass allTenants=true.", "inputSchema": schema({"namespace": {"type": "string"}, "allTenants": {"type": "boolean", "default": False}})},
        {"name": "get_tenant_database_credentials", "description": "Return decoded PostgreSQL credential Secret data for a tenant database. Normal token can only read this tenant namespace and cataloged databases.", "inputSchema": schema({"namespace": {"type": "string"}, "database": {"type": "string"}}, ["database"])},
        {"name": "get_tenant_web_logins", "description": "Return tenant web/robot logins stored in tenant namespace Secrets, including CloudBeaver, Grafana, and Harbor credentials.", "inputSchema": schema({"namespace": {"type": "string"}})},
        {"name": "k8s_api_proxy", "description": "Forward one request to the internal Kubernetes API server using the caller-provided kubeToken. Ops MCP only solves reachability; Kubernetes RBAC is enforced by kubeToken. Path must be a Kubernetes API path such as /api/v1/namespaces/anycam/pods.", "inputSchema": schema({"method": {"type": "string", "default": "GET"}, "path": {"type": "string"}, "kubeToken": {"type": "string"}, "body": {}, "contentType": {"type": "string", "default": "application/json"}, "timeoutSeconds": {"type": "integer", "default": 20}}, ["path", "kubeToken"])},
        {"name": "postgres_sql_proxy", "description": "Execute lightweight PostgreSQL SQL from the Ops MCP network position, useful when external agents cannot resolve or reach pgwrite.zt.re8ch.com. Default route is instancePrimaryName, currently pgwrite.zt.re8ch.com:5432, because tenant roles authenticate there reliably. Default is readOnly=true and only allows SELECT/WITH/SHOW/EXPLAIN/VALUES. PostgreSQL credentials and tenant catalog still enforce access.", "inputSchema": schema({"namespace": {"type": "string"}, "database": {"type": "string"}, "sql": {"type": "string"}, "role": {"type": "string", "default": "app_ro"}, "route": {"type": "string", "default": "instancePrimaryName"}, "readOnly": {"type": "boolean", "default": True}, "user": {"type": "string"}, "password": {"type": "string"}, "maxRows": {"type": "integer", "default": 100}, "timeoutSeconds": {"type": "integer", "default": 10}}, ["database", "sql"])},
    ]
    if include_admin:
        tools.extend([
            {"name": "list_authentik_users", "description": "Admin: list Authentik users and group names without sessions, tokens, password material, or other credentials.", "inputSchema": schema()},
            {"name": "ensure_authentik_user", "description": "Admin: create or update an Authentik user and reviewed group memberships without accepting or returning a password.", "inputSchema": schema({"username": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"}, "groups": {"type": "array", "items": {"type": "string"}}, "isActive": {"type": "boolean", "default": True}, "path": {"type": "string", "default": "users"}}, ["username"])},
            {"name": "grant_authentik_user_group", "description": "Admin: grant an existing Authentik user membership in an existing reviewed group.", "inputSchema": schema({"username": {"type": "string"}, "email": {"type": "string"}, "group": {"type": "string"}}, ["group"])},
            {"name": "list_dex_users", "description": "Admin: list Dex static users without password hashes.", "inputSchema": schema()},
            {"name": "get_tenancy_admin_workflow", "description": "Admin: explain the standard tenant/user access workflow for Headlamp, Dex OIDC, RBAC, and robot kubeconfig.", "inputSchema": schema()},
            {"name": "get_dex_hash_instructions", "description": "Admin: return commands for generating Dex bcrypt passwordHash.", "inputSchema": schema()},
            {"name": "generate_pg_tenant_database_plan", "description": "Admin: generate the standard Pigsty PostgreSQL tenant database plan, roles, secret paths, SQL, and connection routes. This does not execute SQL.", "inputSchema": schema({"database": {"type": "string"}, "tenant": {"type": "string"}, "schema": {"type": "string", "default": "app"}, "generatePasswords": {"type": "boolean", "default": False}, "grantCreateRole": {"type": "boolean", "default": False}, "passwords": {"type": "object"}}, ["database"])},
            {"name": "ensure_tenant_database_secrets", "description": "Admin: create or repair the standard per-database PostgreSQL credential Secret in a tenant namespace. Provide passwords or set generatePasswords=true.", "inputSchema": schema({"namespace": {"type": "string"}, "tenant": {"type": "string"}, "database": {"type": "string"}, "passwords": {"type": "object"}, "generatePasswords": {"type": "boolean", "default": False}, "readHost": {"type": "string"}}, ["namespace", "database"])},
            {"name": "ensure_tenant_database_bundle", "description": "Admin: produce the Pigsty database plan and, when credentials are provided/generated, write the standard database Secret and CloudBeaver access expectations.", "inputSchema": schema({"namespace": {"type": "string"}, "tenant": {"type": "string"}, "database": {"type": "string"}, "schema": {"type": "string", "default": "app"}, "passwords": {"type": "object"}, "generatePasswords": {"type": "boolean", "default": False}, "grantCreateRole": {"type": "boolean", "default": False}, "readHost": {"type": "string"}}, ["namespace", "database"])},
            {"name": "ensure_tenant_access", "description": "Admin: create namespace, tenant roles, OIDC group RoleBinding, robot ServiceAccount token, and cluster read-only bindings.", "inputSchema": schema({"namespace": {"type": "string"}, "group": {"type": "string"}, "serviceAccount": {"type": "string"}, "createNamespace": {"type": "boolean", "default": True}}, ["namespace"])},
            {"name": "create_dex_user", "description": "Admin: create a Dex static user and optionally grant namespace operator access. Requires passwordHash, not plaintext password. Can also reconcile the matching Harbor tenant when ensureHarbor=true.", "inputSchema": schema({"email": {"type": "string"}, "username": {"type": "string"}, "userID": {"type": "string"}, "passwordHash": {"type": "string"}, "namespace": {"type": "string"}, "group": {"type": "string"}, "groups": {"type": "array", "items": {"type": "string"}}, "createNamespace": {"type": "boolean", "default": True}, "updateExisting": {"type": "boolean", "default": False}, "ensureHarbor": {"type": "boolean", "default": False}, "harborPassword": {"type": "string"}, "rotateHarborRobot": {"type": "boolean", "default": False}}, ["email", "passwordHash"])},
            {"name": "grant_user_namespace", "description": "Admin: grant an existing Dex static user operator access to a namespace and reconcile tenant RBAC.", "inputSchema": schema({"email": {"type": "string"}, "namespace": {"type": "string"}, "group": {"type": "string"}, "createNamespace": {"type": "boolean", "default": True}}, ["email", "namespace"])},
            {"name": "ensure_harbor_tenant", "description": "Admin: create or repair the matching Harbor project, Harbor web user, projectAdmin membership, project admin robot, and Kubernetes image secret. Defaults require namespace/project/username to be the same.", "inputSchema": schema({"namespace": {"type": "string"}, "tenant": {"type": "string"}, "project": {"type": "string"}, "email": {"type": "string"}, "username": {"type": "string"}, "harborPassword": {"type": "string"}, "createUser": {"type": "boolean", "default": True}, "roleID": {"type": "integer", "default": 1}, "rotateRobot": {"type": "boolean", "default": False}, "allowDifferentProject": {"type": "boolean", "default": False}, "allowDifferentUsername": {"type": "boolean", "default": False}}, [])},
            {"name": "set_tool_publication_stage", "description": "Admin: set canary, internal, or deprecated stage for one reviewed ToolHive publication; arbitrary resources cannot be patched.", "inputSchema": schema({"server": {"type": "string", "enum": sorted(MANAGED_TOOL_PUBLICATIONS)}, "stage": {"type": "string", "enum": sorted(PUBLICATION_STAGES)}}, ["server", "stage"])},
            {"name": "restart_managed_platform_deployment", "description": "Operate: restart one pre-reviewed platform Deployment. The tool allowlist and namespaced resourceNames RBAC enforce the target.", "inputSchema": schema({"namespace": {"type": "string", "enum": sorted({item[0] for item in MANAGED_PLATFORM_RESTARTS})}, "deployment": {"type": "string"}}, ["namespace", "deployment"])},
            {"name": "scale_managed_platform_deployment", "description": "Operate: scale one pre-reviewed platform Deployment to exactly zero or one replica.", "inputSchema": schema({"namespace": {"type": "string", "enum": sorted({item[0] for item in MANAGED_PLATFORM_RESTARTS})}, "deployment": {"type": "string"}, "replicas": {"type": "integer", "enum": [0, 1]}}, ["namespace", "deployment", "replicas"])},
        ])
    write_tools = {
        "restart_deployment",
        "ensure_authentik_user",
        "grant_authentik_user_group",
        "ensure_tenant_database_secrets",
        "ensure_tenant_database_bundle",
        "ensure_tenant_access",
        "create_dex_user",
        "grant_user_namespace",
        "ensure_harbor_tenant",
        "set_tool_publication_stage",
        "restart_managed_platform_deployment",
        "scale_managed_platform_deployment",
        "k8s_api_proxy",
        "postgres_sql_proxy",
    }
    destructive_tools = {"set_tool_publication_stage", "scale_managed_platform_deployment", "k8s_api_proxy", "postgres_sql_proxy"}
    for tool in tools:
        name = tool["name"]
        tool["annotations"] = {
            "readOnlyHint": name not in write_tools,
            "openWorldHint": False,
            "destructiveHint": name in destructive_tools,
        }
        # Dex is the only issuer. Fine-grained authorization is derived from
        # its groups claim at the vMCP edge, not from synthetic OAuth scopes.
        tool["securitySchemes"] = [{"type": "oauth2", "scopes": ["openid"]}]
    profile_tools = {
        "public": OBSERVE_TOOL_NAMES,
        "observe": OBSERVE_TOOL_NAMES,
        "operate": OPERATE_TOOL_NAMES,
        "admin": ADMIN_TOOL_NAMES,
    }.get(OPS_TOOL_PROFILE)
    return [tool for tool in tools if profile_tools is None or tool["name"] in profile_tools]

def call_tool(name, args, admin=False):
    args = args or {}
    profile_tools = {
        "public": OBSERVE_TOOL_NAMES,
        "observe": OBSERVE_TOOL_NAMES,
        "operate": OPERATE_TOOL_NAMES,
        "admin": ADMIN_TOOL_NAMES,
    }.get(OPS_TOOL_PROFILE)
    if profile_tools is not None and name not in profile_tools:
        raise PermissionError("tool is not available in the %s profile: %s" % (OPS_TOOL_PROFILE, name))
    if name == "get_public_tool_service_info":
        return {
            "name": "RE8CH Platform Observe",
            "mcpResource": "https://tools.re8ch.com/mcp",
            "authorizationServer": "https://auth.re8ch.com/application/o/toolhive-platform-observe/",
            "access": "OAuth 2.0 authorization code with PKCE S256",
            "scope": "platform:observe",
            "publication": "ToolHive-managed read-only canary",
            "clients": ["ChatGPT and Codex connectors", "Claude Code remote HTTP MCP", "standards-compliant MCP clients"],
            "safety": "This surface excludes write/admin tools, Secret values, kubeconfig, arbitrary SQL, and generic Kubernetes API access.",
        }
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
    if name == "get_tenant_resource_catalog":
        return tenant_resource_catalog(all_tenants=bool(args.get("allTenants", False)) and admin)
    if name == "get_grafana_links":
        return grafana_links()
    if name == "get_grafana_access":
        return grafana_access()
    if name == "get_tenant_metrics_summary":
        return get_tenant_metrics_summary()
    if name == "get_platform_service_health":
        return get_platform_service_health()
    if name == "get_platform_service_catalog":
        return get_platform_service_catalog()
    if name == "get_platform_permission_coverage":
        return get_platform_permission_coverage()
    if name == "get_platform_access_posture":
        return get_platform_access_posture()
    if name == "get_managed_platform_operation_status":
        return get_managed_platform_operation_status()
    if name == "get_nuclio_function_status":
        return get_nuclio_function_status()
    if name == "diagnose_nuclio_function":
        return diagnose_nuclio_function(args)
    if name == "get_nuclio_capacity_status":
        return get_nuclio_capacity_status()
    if name == "get_litellm_gateway_status":
        return get_litellm_gateway_status()
    if name == "get_fleet_control_status":
        return get_fleet_control_status()
    if name == "get_supabase_platform_status":
        return get_supabase_platform_status()
    if name == "get_neo4j_platform_status":
        return get_neo4j_platform_status()
    if name == "get_identity_platform_status":
        return get_identity_platform_status()
    if name == "get_agent_runtime_status":
        return get_agent_runtime_status()
    if name == "get_observability_platform_status":
        return get_observability_platform_status()
    if name == "get_artifact_platform_status":
        return get_artifact_platform_status()
    if name == "get_database_routing_status":
        return get_database_routing_status()
    if name == "get_external_publication_readiness":
        return get_external_publication_readiness()
    if name == "get_tenant_secret_refs":
        return get_tenant_secret_refs(args, admin=admin)
    if name == "get_tenant_database_credentials":
        return get_tenant_database_credentials(args, admin=admin)
    if name == "get_tenant_web_logins":
        return get_tenant_web_logins(args, admin=admin)
    if name == "k8s_api_proxy":
        return k8s_api_proxy(args)
    if name == "postgres_sql_proxy":
        return postgres_sql_proxy(args, admin=admin)
    admin_tools = {"list_authentik_users", "ensure_authentik_user", "grant_authentik_user_group", "list_dex_users", "get_tenancy_admin_workflow", "get_dex_hash_instructions", "generate_pg_tenant_database_plan", "ensure_tenant_database_secrets", "ensure_tenant_database_bundle", "ensure_tenant_access", "create_dex_user", "grant_user_namespace", "ensure_harbor_tenant", "set_tool_publication_stage"}
    if name in admin_tools and not admin:
        raise PermissionError("admin token required for tool: %s" % name)
    if name == "list_dex_users":
        return list_dex_users()
    if name == "list_authentik_users":
        return list_authentik_users()
    if name == "ensure_authentik_user":
        return ensure_authentik_user(args)
    if name == "grant_authentik_user_group":
        return grant_authentik_user_group(args)
    if name == "get_tenancy_admin_workflow":
        return get_tenancy_admin_workflow()
    if name == "get_dex_hash_instructions":
        return get_dex_hash_instructions()
    if name == "generate_pg_tenant_database_plan":
        return generate_pg_tenant_database_plan(args)
    if name == "ensure_tenant_database_secrets":
        return ensure_tenant_database_secrets(args)
    if name == "ensure_tenant_database_bundle":
        return ensure_tenant_database_bundle(args)
    if name == "ensure_tenant_access":
        return ensure_tenant_access(args.get("namespace"), group=args.get("group"), serviceaccount=args.get("serviceAccount"), create_namespace=bool(args.get("createNamespace", True)))
    if name == "create_dex_user":
        return create_dex_user(args)
    if name == "grant_user_namespace":
        return grant_user_namespace(args)
    if name == "ensure_harbor_tenant":
        return ensure_harbor_tenant_identity(args)
    if name == "set_tool_publication_stage":
        return set_tool_publication_stage(args)
    if name == "restart_managed_platform_deployment":
        return restart_managed_platform_deployment(args)
    if name == "scale_managed_platform_deployment":
        return scale_managed_platform_deployment(args)
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

    def trusted_loopback_proxy(self):
        source = ipaddress.ip_address(self.client_address[0])
        if TRUST_LOOPBACK_PROXY and source.is_loopback:
            return True
        return any(source in network for network in TRUST_PROXY_CIDRS)

    def tenant_token_map(self):
        mapping = {}
        if OPS_TOKEN:
            mapping[OPS_TOKEN] = TENANT
        try:
            doc = kube_request("GET", "/api/v1/namespaces/%s/secrets/%s" % (OPS_AUTH_SECRET_NAMESPACE, OPS_AUTH_SECRET_NAME))
            for key, value in (doc.get("data") or {}).items():
                if key in ("admin-token", "api-token") or not key.endswith("-token"):
                    continue
                tenant = key[:-len("-token")]
                if not tenant or tenant == "admin":
                    continue
                mapping[base64.b64decode(value).decode("utf-8")] = tenant
        except Exception as exc:
            print("failed to load tenant token map: %s" % exc, flush=True)
        return mapping

    def token_tenant(self):
        token = self.presented_token()
        if bool(OPS_ADMIN_TOKEN) and token == OPS_ADMIN_TOKEN:
            return TENANT
        return self.tenant_token_map().get(token)

    def authorized(self):
        if self.trusted_loopback_proxy():
            return OPS_TOOL_PROFILE in ("public", "observe", "operate", "admin")
        token = self.presented_token()
        return bool(token) and (token in [OPS_TOKEN, OPS_ADMIN_TOKEN] or self.token_tenant() is not None)

    def admin_authorized(self):
        if self.trusted_loopback_proxy():
            return OPS_TOOL_PROFILE == "admin"
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

    def send_binary(self, payload, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def require_auth(self):
        if self.authorized():
            # ToolHive already authenticated and authorized the caller. Do not
            # interpret its short-lived KSA token as a legacy tenant token or
            # read the legacy auth Secret from a least-privilege workload.
            if self.trusted_loopback_proxy():
                ACTIVE_TENANT.set(TENANT)
            else:
                ACTIVE_TENANT.set(self.token_tenant() or TENANT)
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
            elif parsed.path == "/api/grafana/render":
                if self.require_auth():
                    params = urllib.parse.parse_qs(parsed.query)
                    args = {key: values[0] for key, values in params.items() if values}
                    rendered = grafana_render_panel(args)
                    self.send_binary(rendered["bytes"], rendered["contentType"])
            elif parsed.path == "/api/metrics/summary":
                if self.require_auth():
                    self.send_json(get_tenant_metrics_summary())
            elif parsed.path == "/api/secrets/refs":
                if self.require_auth():
                    params = urllib.parse.parse_qs(parsed.query)
                    args = {
                        "namespace": params.get("namespace", [None])[0],
                        "allTenants": params.get("allTenants", ["false"])[0].lower() in ("1", "true", "yes"),
                    }
                    self.send_json(get_tenant_secret_refs(args, admin=self.admin_authorized()))
            elif parsed.path == "/api/secrets/web-logins":
                if self.require_auth():
                    params = urllib.parse.parse_qs(parsed.query)
                    self.send_json(get_tenant_web_logins({"namespace": params.get("namespace", [None])[0]}, admin=self.admin_authorized()))
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
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
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
            elif parsed.path == "/api/k8s/proxy":
                self.send_json(k8s_api_proxy(body))
            elif parsed.path == "/api/postgres/query":
                self.send_json(postgres_sql_proxy(body, admin=self.admin_authorized()))
            elif parsed.path == "/api/secrets/database-credentials":
                self.send_json(get_tenant_database_credentials(body, admin=self.admin_authorized()))
            elif parsed.path == "/api/admin/ensure-tenant-access":
                if self.require_admin():
                    self.send_json(ensure_tenant_access(body.get("namespace"), group=body.get("group"), serviceaccount=body.get("serviceAccount"), create_namespace=bool(body.get("createNamespace", True))))
            elif parsed.path == "/api/admin/generate-pg-tenant-database-plan":
                if self.require_admin():
                    self.send_json(generate_pg_tenant_database_plan(body))
            elif parsed.path == "/api/admin/ensure-tenant-database-secrets":
                if self.require_admin():
                    self.send_json(ensure_tenant_database_secrets(body))
            elif parsed.path == "/api/admin/ensure-tenant-database-bundle":
                if self.require_admin():
                    self.send_json(ensure_tenant_database_bundle(body))
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
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def handle_mcp(self, req):
        method = req.get("method")
        request_id = req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "re8ch-ops-mcp", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": mcp_tools(include_admin=self.admin_authorized() or OPS_TOOL_PROFILE in ("operate", "admin"))}
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
