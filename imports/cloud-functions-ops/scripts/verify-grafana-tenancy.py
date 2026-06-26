#!/usr/bin/env python3
"""Verify Grafana org-level tenant isolation.

The verifier is read-only. It can validate service account tokens stored in
Kubernetes Secrets and, when provided through environment variables, tenant
human login credentials.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


TENANTS = {
    "qianwen": {
        "org": "qwen",
        "dashboardUid": "tenant-qianwen-overview",
        "serviceAccountSecret": {"namespace": "qwen", "name": "grafana-qianwen-viewer"},
        "humanEnvPrefix": "QIANWEN",
        "allowedDatasources": {"tenant-metrics", "tenant-vlogs", "observable-events"},
        "forbiddenDatasources": {"pigsty-metrics", "re8ch-vlogs", "pg-meta-1-ledger", "pg-meta-1-anycam-prod", "pg-meta-1-postgres"},
    },
    "ledger": {
        "org": "Tenant ledger",
        "dashboardUid": "tenant-ledger-overview",
        "serviceAccountSecret": {"namespace": "ledger", "name": "grafana-ledger-viewer"},
        "humanEnvPrefix": "LEDGER",
        "allowedDatasources": {"tenant-metrics", "tenant-vlogs", "observable-events"},
        "forbiddenDatasources": {"pigsty-metrics", "re8ch-vlogs", "pg-meta-1-qianwen-prod", "pg-meta-1-anycam-prod", "pg-meta-1-postgres"},
    },
}


class Auth:
    def __init__(self, header: str, label: str):
        self.header = header
        self.label = label


def kubectl(args: list[str]) -> str:
    cmd = os.environ.get("KUBECTL_CMD", "kubectl").split() + args
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def secret_key(namespace: str, secret: str, key: str) -> str:
    raw = kubectl(["-n", namespace, "get", "secret", secret, "-o", "jsonpath={.data.%s}" % key])
    return base64.b64decode(raw).decode()


def basic_auth(username: str, password: str, label: str) -> Auth:
    raw = ("%s:%s" % (username, password)).encode()
    return Auth("Basic " + base64.b64encode(raw).decode(), label)


def bearer_auth(token: str, label: str) -> Auth:
    return Auth("Bearer " + token, label)


def request(base_url: str, auth: Auth, path: str, org_id: int | None = None, ok: set[int] | None = None) -> tuple[int, Any]:
    ok = ok or {200}
    headers = {"Authorization": auth.header, "Accept": "application/json"}
    if org_id is not None:
        headers["X-Grafana-Org-Id"] = str(org_id)
    req = urllib.request.Request(base_url.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode()
            payload = json.loads(raw) if raw else {}
            if response.status not in ok:
                raise RuntimeError("%s %s returned HTTP %s" % (auth.label, path, response.status))
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        if exc.code in ok:
            return exc.code, payload
        raise RuntimeError("%s %s returned HTTP %s: %s" % (auth.label, path, exc.code, payload))


def admin_auth(args: argparse.Namespace) -> Auth:
    username = args.admin_user or secret_key(args.namespace, args.admin_secret, "username")
    password = args.admin_password or secret_key(args.namespace, args.admin_secret, "password")
    return basic_auth(username, password, "admin")


def org_id(base_url: str, auth: Auth, org_name: str) -> int:
    status, payload = request(base_url, auth, "/api/orgs/name/%s" % urllib.parse.quote(org_name, safe=""))
    return int(payload["id"])


def verify_auth_scope(base_url: str, auth: Auth, tenant: str, cfg: dict[str, Any], expected_org_id: int) -> None:
    _, user = request(base_url, auth, "/api/user")
    if int(user.get("orgId", 0)) != expected_org_id:
        raise RuntimeError("%s orgId=%s, expected %s" % (auth.label, user.get("orgId"), expected_org_id))
    _, org = request(base_url, auth, "/api/org")
    if org.get("name") != cfg["org"]:
        raise RuntimeError("%s org=%s, expected %s" % (auth.label, org.get("name"), cfg["org"]))

    _, search = request(base_url, auth, "/api/search?type=dash-db")
    titles = {item.get("uid") or item.get("title") for item in search}
    if cfg["dashboardUid"] not in titles:
        raise RuntimeError("%s cannot see tenant dashboard %s" % (auth.label, cfg["dashboardUid"]))
    if any(str(item.get("folderTitle", "")).startswith("Pigsty") for item in search):
        raise RuntimeError("%s can see Pigsty folder dashboards" % auth.label)

    _, datasources = request(base_url, auth, "/api/datasources")
    uids = {item.get("uid") for item in datasources}
    missing = cfg["allowedDatasources"] - uids
    if missing:
        raise RuntimeError("%s missing tenant datasources: %s" % (auth.label, sorted(missing)))
    forbidden = cfg["forbiddenDatasources"].intersection(uids) or {uid for uid in uids if str(uid).startswith("pg-meta-")}
    if forbidden:
        raise RuntimeError("%s can see forbidden datasources: %s" % (auth.label, sorted(forbidden)))

    code, _ = request(base_url, auth, "/api/org", org_id=1, ok={200, 401, 403})
    if expected_org_id != 1 and code == 200:
        raise RuntimeError("%s can query Main Org with X-Grafana-Org-Id: 1" % auth.label)
    print("grafana_scope=ok auth=%s tenant=%s orgId=%s" % (auth.label, tenant, expected_org_id))


def tenant_service_auth(cfg: dict[str, Any]) -> Auth | None:
    secret = cfg["serviceAccountSecret"]
    try:
        token = secret_key(secret["namespace"], secret["name"], "token")
    except Exception as exc:
        print("service_account_token=missing namespace=%s name=%s error=%s" % (secret["namespace"], secret["name"], exc))
        return None
    return bearer_auth(token, "service-account:%s" % secret["name"])


def tenant_human_auth(cfg: dict[str, Any]) -> Auth | None:
    prefix = cfg["humanEnvPrefix"]
    username = os.environ.get("%s_GRAFANA_USER" % prefix, "")
    password = os.environ.get("%s_GRAFANA_PASSWORD" % prefix, "")
    if not username or not password:
        print("human_login=skipped prefix=%s reason=missing_env" % prefix)
        return None
    return basic_auth(username, password, "human:%s" % username)


def verify_admin_regression(base_url: str, auth: Auth) -> None:
    _, org = request(base_url, auth, "/api/org", org_id=1)
    if org.get("name") != "Main Org.":
        raise RuntimeError("admin cannot query Main Org: %s" % org.get("name"))
    _, search = request(base_url, auth, "/api/search?type=dash-db", org_id=1)
    if not any(item.get("folderTitle") == "Pigsty PGSQL" for item in search):
        raise RuntimeError("admin cannot see Pigsty PGSQL dashboards")
    _, datasources = request(base_url, auth, "/api/datasources", org_id=1)
    uids = {item.get("uid") for item in datasources}
    for required in ("pigsty-metrics", "re8ch-vlogs"):
        if required not in uids:
            raise RuntimeError("admin missing datasource %s" % required)
    print("grafana_admin_regression=ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Grafana tenant org isolation.")
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "https://grafana.re8ch.com/ui").rstrip("/"))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "qianwen-ops"))
    parser.add_argument("--admin-secret", default=os.environ.get("ADMIN_SECRET", "panel-grafana-admin-recovery"))
    parser.add_argument("--admin-user", default=os.environ.get("GRAFANA_ADMIN_USER", ""))
    parser.add_argument("--admin-password", default=os.environ.get("GRAFANA_ADMIN_PASSWORD", ""))
    parser.add_argument("--tenant", action="append", choices=sorted(TENANTS), help="Tenant to verify. Repeatable. Default: qianwen and ledger.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    admin = admin_auth(args)
    verify_admin_regression(args.grafana_url, admin)
    for tenant in args.tenant or sorted(TENANTS):
        cfg = TENANTS[tenant]
        expected_org_id = org_id(args.grafana_url, admin, cfg["org"])
        for auth in (tenant_service_auth(cfg), tenant_human_auth(cfg)):
            if auth is not None:
                verify_auth_scope(args.grafana_url, auth, tenant, cfg, expected_org_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
