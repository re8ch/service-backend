#!/usr/bin/env python3
"""Reconcile Grafana org-level tenant isolation.

This script intentionally reads admin credentials from Kubernetes Secrets or
environment variables. It never stores human passwords or Grafana tokens in the
repository. By default it runs in dry-run mode; pass --apply to mutate Grafana.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DASHBOARD_DIR = ROOT / "manifests/k3s/qianwen-ops/grafana-dashboards/tenants"
PIGSTY_DASHBOARD_FOLDER = "Pigsty PGSQL"
FORBIDDEN_TENANT_DATASOURCES = {
    "pigsty-metrics",
    "re8ch-vlogs",
    "re8ch-loki",
    "ds-prometheus",
    "ds-static",
}
TENANT_DB_METRIC_PREFIXES = (
    "pg_db",
    "pg:db:",
    "pg_activity",
    "pg_size",
    "pg_query",
    "pg_table",
    "pg_index",
    "pg_class",
    "pg_namespace",
    "pg_stat",
)


TENANTS: dict[str, dict[str, Any]] = {
    "qianwen": {
        # Existing Grafana org name is qwen. Metrics/logs/Observable tenant
        # scope stays qianwen for historical compatibility.
        "org": "qwen",
        "users": [{"loginOrEmail": "zhangrh@ynzs.com", "role": "Viewer"}],
        "serviceAccount": "qianwen-ai-agent-viewer",
        "serviceAccountSecret": {"namespace": "qwen", "name": "grafana-qianwen-viewer"},
        "dashboard": "tenant-qianwen-overview.json",
        "metricsPath": "qianwen",
        "logsPath": "qianwen",
        "observableTenant": "qianwen",
        "pgMonitorSecret": "panel-grafana-pg-monitor",
        "pgInstances": [
            {"ins": "pg-meta-1", "url": "192.0.2.89:5432"},
            {"ins": "pg-meta-2", "url": "192.0.2.112:5432"},
            {"ins": "pg-meta-3", "url": "192.0.2.251:5432"},
        ],
        "pgDatabases": ["qianwen_prod", "qianwen_test", "cardcat"],
        "pigstyDashboards": True,
    },
    "ledger": {
        "org": "Tenant ledger",
        "users": [{"loginOrEmail": "ledger", "role": "Viewer"}],
        "serviceAccount": "ledger-ai-agent-viewer",
        "serviceAccountSecret": {"namespace": "ledger", "name": "grafana-ledger-viewer"},
        "dashboard": "tenant-ledger-overview.json",
        "metricsPath": "ledger",
        "logsPath": "ledger",
        "observableTenant": "ledger",
        "pgMonitorSecret": "panel-grafana-pg-monitor",
        "pgInstances": [
            {"ins": "pg-meta-1", "url": "192.0.2.89:5432"},
            {"ins": "pg-meta-2", "url": "192.0.2.112:5432"},
            {"ins": "pg-meta-3", "url": "192.0.2.251:5432"},
        ],
        "pgDatabases": ["ledger"],
        "pigstyDashboards": True,
    },
}


def now_suffix() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")


def info(message: str) -> None:
    print(message, flush=True)


class GrafanaError(RuntimeError):
    pass


class GrafanaClient:
    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = ""):
        self.base_url = base_url.rstrip("/")
        if token:
            self.auth = "Bearer " + token
        else:
            raw = ("%s:%s" % (username, password)).encode()
            self.auth = "Basic " + base64.b64encode(raw).decode()

    def request(self, method: str, path: str, body: Any | None = None, org_id: int | None = None, ok: set[int] | None = None) -> tuple[int, Any]:
        ok = ok or {200}
        headers = {"Authorization": self.auth, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        if org_id is not None:
            headers["X-Grafana-Org-Id"] = str(org_id)
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode()
                payload = json.loads(raw) if raw else {}
                if response.status not in ok:
                    raise GrafanaError("%s %s returned HTTP %s: %s" % (method, path, response.status, payload))
                return response.status, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw
            if exc.code in ok:
                return exc.code, payload
            raise GrafanaError("%s %s returned HTTP %s: %s" % (method, path, exc.code, payload))

    def get_org_by_name(self, name: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(name, safe="")
        status, payload = self.request("GET", "/api/orgs/name/%s" % quoted, ok={200, 404})
        if status == 404:
            return None
        return payload

    def ensure_org(self, name: str, apply: bool) -> int:
        existing = self.get_org_by_name(name)
        if existing:
            org_id = int(existing["id"])
            info("org=present name=%s id=%s" % (name, org_id))
            return org_id
        if not apply:
            info("org=would_create name=%s" % name)
            return -1
        _, payload = self.request("POST", "/api/orgs", {"name": name}, ok={200})
        org_id = int(payload["orgId"])
        info("org=created name=%s id=%s" % (name, org_id))
        return org_id

    def lookup_user(self, login_or_email: str) -> dict[str, Any] | None:
        path = "/api/users/lookup?loginOrEmail=" + urllib.parse.quote(login_or_email, safe="")
        status, payload = self.request("GET", path, ok={200, 404})
        if status == 404:
            return None
        return payload

    def org_users(self, org_id: int) -> list[dict[str, Any]]:
        _, payload = self.request("GET", "/api/orgs/%d/users" % org_id)
        return payload

    def ensure_user_in_org(self, org_id: int, login_or_email: str, role: str, apply: bool) -> int | None:
        user = self.lookup_user(login_or_email)
        if not user:
            info("user=missing loginOrEmail=%s" % login_or_email)
            return None
        user_id = int(user["id"])
        members = self.org_users(org_id)
        member = next((item for item in members if int(item.get("userId") or item.get("userID") or 0) == user_id), None)
        if member and member.get("role") == role:
            info("org_user=present org=%s user=%s role=%s" % (org_id, login_or_email, role))
            return user_id
        if not apply:
            action = "would_update" if member else "would_add"
            info("org_user=%s org=%s user=%s role=%s" % (action, org_id, login_or_email, role))
            return user_id
        if member:
            self.request("PATCH", "/api/orgs/%d/users/%d" % (org_id, user_id), {"role": role})
            info("org_user=updated org=%s user=%s role=%s" % (org_id, login_or_email, role))
        else:
            self.request("POST", "/api/orgs/%d/users" % org_id, {"loginOrEmail": login_or_email, "role": role}, ok={200})
            info("org_user=added org=%s user=%s role=%s" % (org_id, login_or_email, role))
        return user_id

    def remove_user_from_org(self, org_id: int, user_id: int, login_or_email: str, apply: bool) -> None:
        members = self.org_users(org_id)
        if not any(int(item.get("userId") or item.get("userID") or 0) == user_id for item in members):
            info("main_org_user=absent user=%s" % login_or_email)
            return
        if not apply:
            info("main_org_user=would_remove user=%s" % login_or_email)
            return
        try:
            self.request("DELETE", "/api/orgs/%d/users/%d" % (org_id, user_id), ok={200})
        except GrafanaError:
            self.request("DELETE", "/api/org/users/%d" % user_id, org_id=org_id, ok={200})
        info("main_org_user=removed user=%s" % login_or_email)

    def upsert_datasource(self, org_id: int, datasource: dict[str, Any], apply: bool) -> None:
        uid = datasource["uid"]
        status, _ = self.request("GET", "/api/datasources/uid/%s" % urllib.parse.quote(uid, safe=""), org_id=org_id, ok={200, 404})
        if not apply:
            info("datasource=%s org=%s action=%s" % (uid, org_id, "would_update" if status == 200 else "would_create"))
            return
        if status == 200:
            self.request("PUT", "/api/datasources/uid/%s" % urllib.parse.quote(uid, safe=""), datasource, org_id=org_id, ok={200})
            info("datasource=%s org=%s action=updated" % (uid, org_id))
        else:
            self.request("POST", "/api/datasources", datasource, org_id=org_id, ok={200})
            info("datasource=%s org=%s action=created" % (uid, org_id))

    def list_datasources(self, org_id: int) -> list[dict[str, Any]]:
        _, payload = self.request("GET", "/api/datasources", org_id=org_id)
        return payload

    def delete_datasource(self, org_id: int, uid: str, apply: bool) -> None:
        if not apply:
            info("datasource=%s org=%s action=would_delete" % (uid, org_id))
            return
        self.request("DELETE", "/api/datasources/uid/%s" % urllib.parse.quote(uid, safe=""), org_id=org_id, ok={200})
        info("datasource=%s org=%s action=deleted" % (uid, org_id))

    def service_accounts(self, org_id: int, query: str) -> list[dict[str, Any]]:
        path = "/api/serviceaccounts/search?query=" + urllib.parse.quote(query, safe="")
        _, payload = self.request("GET", path, org_id=org_id)
        return payload.get("serviceAccounts", [])

    def ensure_service_account(self, org_id: int, name: str, role: str, apply: bool) -> int | None:
        accounts = self.service_accounts(org_id, name)
        account = next((item for item in accounts if item.get("name") == name), None)
        if account:
            account_id = int(account["id"])
            info("service_account=present org=%s name=%s id=%s" % (org_id, name, account_id))
            return account_id
        if not apply:
            info("service_account=would_create org=%s name=%s role=%s" % (org_id, name, role))
            return None
        _, payload = self.request("POST", "/api/serviceaccounts", {"name": name, "role": role}, org_id=org_id, ok={201, 200})
        account_id = int(payload["id"])
        info("service_account=created org=%s name=%s id=%s" % (org_id, name, account_id))
        return account_id

    def rotate_service_account_token(self, org_id: int, account_id: int, token_name: str, apply: bool) -> str | None:
        if not apply:
            info("service_account_token=would_rotate org=%s account=%s" % (org_id, account_id))
            return None
        _, payload = self.request(
            "POST",
            "/api/serviceaccounts/%d/tokens" % account_id,
            {"name": token_name, "secondsToLive": 0},
            org_id=org_id,
            ok={200},
        )
        token = payload.get("key")
        if not token:
            raise GrafanaError("Grafana did not return service account token key")
        info("service_account_token=rotated org=%s account=%s name=%s" % (org_id, account_id, token_name))
        return token

    def import_dashboard(self, org_id: int, dashboard_path: Path, apply: bool) -> None:
        with dashboard_path.open() as handle:
            dashboard = json.load(handle)
        dashboard["id"] = None
        self.import_dashboard_payload(org_id, dashboard, "reconcile tenant org dashboard", apply)

    def import_dashboard_payload(self, org_id: int, dashboard: dict[str, Any], message: str, apply: bool) -> None:
        dashboard["id"] = None
        body = {"dashboard": dashboard, "overwrite": True, "message": message}
        uid = dashboard.get("uid", "unknown")
        if not apply:
            info("dashboard=%s org=%s action=would_import" % (uid, org_id))
            return
        self.request("POST", "/api/dashboards/db", body, org_id=org_id, ok={200})
        info("dashboard=%s org=%s action=imported" % (uid, org_id))

    def search_dashboards(self, org_id: int, query: str = "", folder_title: str | None = None) -> list[dict[str, Any]]:
        path = "/api/search?type=dash-db&query=" + urllib.parse.quote(query, safe="")
        _, payload = self.request("GET", path, org_id=org_id)
        if folder_title is None:
            return payload
        return [item for item in payload if item.get("folderTitle") == folder_title]

    def get_dashboard(self, org_id: int, uid: str) -> dict[str, Any]:
        _, payload = self.request("GET", "/api/dashboards/uid/%s" % urllib.parse.quote(uid, safe=""), org_id=org_id)
        return payload["dashboard"]


def kubectl(args: list[str], input_text: str | None = None) -> str:
    cmd = os.environ.get("KUBECTL_CMD", "kubectl").split() + args
    proc = subprocess.run(cmd, input=input_text, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def secret_key(namespace: str, secret: str, key: str) -> str:
    raw = kubectl(["-n", namespace, "get", "secret", secret, "-o", "jsonpath={.data.%s}" % key])
    return base64.b64decode(raw).decode()


def admin_credentials(args: argparse.Namespace) -> tuple[str, str]:
    if args.admin_user and args.admin_password:
        return args.admin_user, args.admin_password
    user = secret_key(args.namespace, args.admin_secret, "username")
    password = secret_key(args.namespace, args.admin_secret, "password")
    return user, password


def tenant_datasources(tenant: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_path = cfg["metricsPath"]
    logs_path = cfg["logsPath"]
    observable_tenant = cfg["observableTenant"]
    return [
        {
            "name": "Tenant Metrics",
            "uid": "tenant-metrics",
            "type": "prometheus",
            "access": "proxy",
            "url": "http://panel-tenant-metrics-proxy.qianwen-ops.svc.cluster.local:39091/%s" % metrics_path,
            "isDefault": True,
            "editable": True,
            "jsonData": {"timeInterval": "30s", "queryTimeout": "30s"},
        },
        {
            "name": "Tenant Logs",
            "uid": "tenant-vlogs",
            "type": "victoriametrics-logs-datasource",
            "access": "proxy",
            "url": "http://panel-tenant-logs-proxy.qianwen-ops.svc.cluster.local:39092/%s" % logs_path,
            "isDefault": False,
            "editable": True,
        },
        {
            "name": "Observable Events",
            "uid": "observable-events",
            "type": "yesoreyeram-infinity-datasource",
            "access": "proxy",
            "url": "https://db-ro.re8ch.com/observable",
            "isDefault": False,
            "editable": True,
            "jsonData": {
                "allowedHosts": ["https://db-ro.re8ch.com"],
                "customHealthCheckEnabled": True,
                "customHealthCheckUrl": "https://db-ro.re8ch.com/observable/observable_events?tenant_id=eq.%s&limit=1" % observable_tenant,
            },
        },
    ]


def tenant_pg_datasources(tenant: str, cfg: dict[str, Any], password: str | None = None) -> list[dict[str, Any]]:
    datasources: list[dict[str, Any]] = []
    for instance in cfg.get("pgInstances", []):
        ins = instance["ins"]
        for database in cfg.get("pgDatabases", []):
            uid = "%s-%s-%s" % (tenant, ins, database.replace("_", "-"))
            datasource = {
                "name": "%s.%s" % (ins, database),
                "uid": uid,
                "type": "grafana-postgresql-datasource",
                "access": "proxy",
                "url": instance["url"],
                "user": "dbuser_monitor",
                "database": database,
                "isDefault": False,
                "editable": True,
                "jsonData": {
                    "database": database,
                    "connMaxLifetime": 3600,
                    "maxIdleConns": 1,
                    "maxOpenConns": 2,
                    "postgresVersion": 1500,
                    "sslmode": "require",
                    "tlsAuth": False,
                    "tlsAuthWithCACert": False,
                },
            }
            if password:
                datasource["secureJsonData"] = {"password": password}
            datasources.append(datasource)
    return datasources


def datasource_uid(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("uid") or "")
    if isinstance(value, str):
        return value
    return ""


def patch_datasource_ref(value: Any) -> int:
    changes = 0
    if isinstance(value, dict):
        uid = value.get("uid")
        if uid in {"ds-prometheus", "pigsty-metrics"}:
            value["uid"] = "tenant-metrics"
            value["type"] = "prometheus"
            changes += 1
        elif uid in {"ds-vlogs", "re8ch-vlogs", "re8ch-loki"}:
            value["uid"] = "tenant-vlogs"
            value["type"] = "victoriametrics-logs-datasource"
            changes += 1
        elif uid in {"$dsn", "${dsn}"}:
            value["uid"] = "${dsn}"
            value["type"] = "grafana-postgresql-datasource"
            changes += 1
        for child in value.values():
            changes += patch_datasource_ref(child)
    elif isinstance(value, list):
        for child in value:
            changes += patch_datasource_ref(child)
    elif isinstance(value, str) and "orgId=1" in value:
        changes += 1
    return changes


def strip_main_org_link(value: Any) -> Any:
    if isinstance(value, str):
        value = value.replace("?orgId=1&", "?")
        value = value.replace("&orgId=1&", "&")
        value = value.replace("?orgId=1", "")
        value = value.replace("&orgId=1", "")
        return value
    if isinstance(value, dict):
        return {key: strip_main_org_link(child) for key, child in value.items()}
    if isinstance(value, list):
        return [strip_main_org_link(child) for child in value]
    return value


def panel_uses_static(panel: dict[str, Any]) -> bool:
    if datasource_uid(panel.get("datasource")) == "ds-static":
        return True
    targets = panel.get("targets") or []
    return bool(targets) and all(datasource_uid(target.get("datasource")) == "ds-static" for target in targets)


def patch_panels(panels: list[dict[str, Any]]) -> int:
    changes = 0
    for panel in panels:
        if "panels" in panel:
            changes += patch_panels(panel.get("panels") or [])
        if panel_uses_static(panel):
            panel["type"] = "text"
            panel["datasource"] = {"type": "grafana", "uid": "-- Grafana --"}
            panel["targets"] = []
            panel["options"] = {
                "mode": "markdown",
                "content": "Static Pigsty metadata card removed from the tenant view.",
            }
            changes += 1
    return changes


def tenantize_promql_expr(expr: str, tenant: str) -> str:
    def replace_selector(match: Any) -> str:
        metric = match.group("metric")
        labels = match.group("labels")
        if not metric.startswith(TENANT_DB_METRIC_PREFIXES):
            return match.group(0)
        if re.search(r'\btenant\s*=~?\s*"', labels):
            return match.group(0)
        labels = labels.strip()
        if labels:
            labels = 'tenant="%s",%s' % (tenant, labels)
        else:
            labels = 'tenant="%s"' % tenant
        return "%s{%s}" % (metric, labels)

    return re.sub(r'(?P<metric>\b[a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{(?P<labels>[^{}]*)\}', replace_selector, expr)


def patch_promql_targets(value: Any, tenant: str) -> int:
    changes = 0
    if isinstance(value, dict):
        expr = value.get("expr")
        if isinstance(expr, str):
            patched = tenantize_promql_expr(expr, tenant)
            if patched != expr:
                value["expr"] = patched
                changes += 1
        for child in value.values():
            changes += patch_promql_targets(child, tenant)
    elif isinstance(value, list):
        for child in value:
            changes += patch_promql_targets(child, tenant)
    return changes


def tenantize_pigsty_dashboard(dashboard: dict[str, Any], tenant: str) -> dict[str, Any]:
    dashboard = strip_main_org_link(json.loads(json.dumps(dashboard)))
    uid = dashboard.get("uid") or ""
    dashboard["id"] = None
    dashboard["uid"] = "tenant-%s-%s" % (tenant, uid)
    dashboard["title"] = "%s - %s" % (tenant, dashboard.get("title", uid))
    dashboard["folderUid"] = ""
    dashboard.pop("folderId", None)
    dashboard.pop("folderTitle", None)
    dashboard.pop("version", None)
    patch_datasource_ref(dashboard)
    patch_panels(dashboard.get("panels") or [])
    patch_promql_targets(dashboard, tenant)
    return dashboard


def apply_k8s_token_secret(cfg: dict[str, Any], token: str | None, apply: bool) -> None:
    secret = cfg["serviceAccountSecret"]
    namespace = secret["namespace"]
    name = secret["name"]
    if not token:
        return
    token_b64 = base64.b64encode(token.encode()).decode()
    type_b64 = base64.b64encode(b"grafana-service-account").decode()
    manifest = """apiVersion: v1
kind: Secret
metadata:
  name: %s
  namespace: %s
  labels:
    app.kubernetes.io/part-of: grafana-tenancy
type: Opaque
data:
  token: %s
  type: %s
""" % (name, namespace, token_b64, type_b64)
    if not apply:
        info("k8s_secret=would_apply namespace=%s name=%s" % (namespace, name))
        return
    kubectl(["apply", "-f", "-"], input_text=manifest)
    info("k8s_secret=applied namespace=%s name=%s" % (namespace, name))


def reconcile_tenant(client: GrafanaClient, tenant: str, cfg: dict[str, Any], args: argparse.Namespace) -> None:
    org_id = args.org_id or client.ensure_org(cfg["org"], args.apply)
    if org_id < 0:
        return

    user_ids: list[tuple[int, str]] = []
    if not args.skip_membership_reconcile:
        for user in cfg.get("users", []):
            user_id = client.ensure_user_in_org(org_id, user["loginOrEmail"], user.get("role", "Viewer"), args.apply)
            if user_id:
                user_ids.append((user_id, user["loginOrEmail"]))
        if args.remove_tenant_users_from_main:
            for user_id, login_or_email in user_ids:
                client.remove_user_from_org(1, user_id, login_or_email, args.apply)

    if not args.skip_datasource_reconcile:
        for datasource in tenant_datasources(tenant, cfg):
            client.upsert_datasource(org_id, datasource, args.apply)

    pg_datasources = tenant_pg_datasources(tenant, cfg)
    if pg_datasources and args.apply and not args.skip_datasource_reconcile:
        pg_password = secret_key(args.namespace, cfg.get("pgMonitorSecret", "panel-grafana-pg-monitor"), "password")
        pg_datasources = tenant_pg_datasources(tenant, cfg, pg_password)
    if not args.skip_datasource_reconcile:
        for datasource in pg_datasources:
            client.upsert_datasource(org_id, datasource, args.apply)

    allowed_uids = {"tenant-metrics", "tenant-vlogs", "observable-events"}
    allowed_uids.update(datasource["uid"] for datasource in pg_datasources)
    if not args.skip_datasource_reconcile:
        for datasource in client.list_datasources(org_id):
            uid = datasource.get("uid", "")
            if uid in allowed_uids:
                continue
            if uid.startswith("pg-meta-") or uid in FORBIDDEN_TENANT_DATASOURCES:
                client.delete_datasource(org_id, uid, args.apply)

    dashboard_path = args.dashboard_dir / cfg["dashboard"]
    if dashboard_path.exists():
        client.import_dashboard(org_id, dashboard_path, args.apply)
    else:
        info("dashboard=missing tenant=%s path=%s" % (tenant, dashboard_path))

    if cfg.get("pigstyDashboards") and args.sync_pigsty_dashboards:
        dashboards = client.search_dashboards(args.main_org_id, folder_title=PIGSTY_DASHBOARD_FOLDER)
        for item in dashboards:
            source = client.get_dashboard(args.main_org_id, item["uid"])
            tenant_dashboard = tenantize_pigsty_dashboard(source, tenant)
            client.import_dashboard_payload(
                org_id,
                tenant_dashboard,
                "sync tenant pigsty dashboard from Main Org",
                args.apply,
            )

    if not args.skip_service_account_reconcile:
        account_id = client.ensure_service_account(org_id, cfg["serviceAccount"], "Viewer", args.apply)
        if account_id and args.rotate_service_account_tokens:
            token = client.rotate_service_account_token(org_id, account_id, "tenant-%s-%s" % (tenant, now_suffix()), args.apply)
            apply_k8s_token_secret(cfg, token, args.apply)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile Grafana tenant org isolation.")
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "https://grafana.re8ch.com/ui").rstrip("/"))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "qianwen-ops"))
    parser.add_argument("--admin-secret", default=os.environ.get("ADMIN_SECRET", "panel-grafana-admin-recovery"))
    parser.add_argument("--admin-user", default=os.environ.get("GRAFANA_ADMIN_USER", ""))
    parser.add_argument("--admin-password", default=os.environ.get("GRAFANA_ADMIN_PASSWORD", ""))
    parser.add_argument("--admin-token", default=os.environ.get("GRAFANA_ADMIN_TOKEN", ""))
    parser.add_argument("--dashboard-dir", type=Path, default=DEFAULT_DASHBOARD_DIR)
    parser.add_argument("--tenant", action="append", choices=sorted(TENANTS), help="Tenant to reconcile. Repeatable. Default: qianwen and ledger.")
    parser.add_argument("--apply", action="store_true", help="Mutate Grafana. Without this flag the script is dry-run.")
    parser.add_argument("--main-org-id", type=int, default=int(os.environ.get("GRAFANA_MAIN_ORG_ID", "1")))
    parser.add_argument("--org-id", type=int, default=int(os.environ.get("GRAFANA_TENANT_ORG_ID", "0")), help="Use an already-known tenant org id and skip org lookup/create.")
    parser.add_argument("--sync-pigsty-dashboards", action="store_true", help="Copy Main Org Pigsty dashboards into tenant orgs with tenant datasource rewrites.")
    parser.add_argument("--skip-membership-reconcile", action="store_true")
    parser.add_argument("--skip-datasource-reconcile", action="store_true")
    parser.add_argument("--skip-service-account-reconcile", action="store_true")
    parser.add_argument("--remove-tenant-users-from-main", action="store_true", default=True)
    parser.add_argument("--keep-tenant-users-in-main", action="store_false", dest="remove_tenant_users_from_main")
    parser.add_argument("--rotate-service-account-tokens", action="store_true", help="Create new tenant service account tokens and store them in K8S Secrets.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.admin_token:
        client = GrafanaClient(args.grafana_url, token=args.admin_token)
    else:
        username, password = admin_credentials(args)
        client = GrafanaClient(args.grafana_url, username, password)
    tenants = args.tenant or sorted(TENANTS)
    for tenant in tenants:
        reconcile_tenant(client, tenant, TENANTS[tenant], args)
    mode = "applied" if args.apply else "dry_run"
    info("grafana_tenancy_reconcile=%s tenants=%s" % (mode, ",".join(tenants)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
