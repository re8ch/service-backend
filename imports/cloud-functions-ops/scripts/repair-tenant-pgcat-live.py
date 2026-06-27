#!/usr/bin/env python3
"""Patch tenant PGCAT dashboards that still use raw Pigsty PostgreSQL panels.

This is a compatibility repair for the current live dashboards while the
Observable PGCAT snapshot path is being brought online. It does not create new
PostgreSQL datasources or broaden database permissions.
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


PGCAT_UIDS = (
    "tenant-qianwen-pgcat-database",
    "tenant-qianwen-pgcat-instance",
    "tenant-qianwen-pgcat-locks",
    "tenant-qianwen-pgcat-query",
    "tenant-qianwen-pgcat-schema",
    "tenant-qianwen-pgcat-table",
)


def info(message: str) -> None:
    print(message, flush=True)


def kubectl(args: list[str]) -> str:
    cmd = os.environ.get("KUBECTL_CMD", "kubectl").split() + args
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def secret_key(namespace: str, secret: str, key: str) -> str:
    raw = kubectl(["-n", namespace, "get", "secret", secret, "-o", "jsonpath={.data.%s}" % key])
    return base64.b64decode(raw).decode()


class Grafana:
    def __init__(self, base_url: str, auth_header: str):
        self.base_url = base_url.rstrip("/")
        self.auth_header = auth_header

    def request(self, method: str, path: str, body: Any | None = None, org_id: int | None = None) -> Any:
        headers = {"Authorization": self.auth_header, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        if org_id is not None:
            headers["X-Grafana-Org-Id"] = str(org_id)
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            raise RuntimeError("%s %s returned HTTP %s: %s" % (method, path, exc.code, raw))

    def get_dashboard(self, org_id: int, uid: str) -> dict[str, Any]:
        payload = self.request("GET", "/api/dashboards/uid/%s" % urllib.parse.quote(uid, safe=""), org_id=org_id)
        return payload["dashboard"]

    def import_dashboard(self, org_id: int, dashboard: dict[str, Any], apply: bool) -> None:
        uid = dashboard.get("uid", "unknown")
        if not apply:
            info("dashboard=%s org=%s action=would_import" % (uid, org_id))
            return
        dashboard["id"] = None
        self.request(
            "POST",
            "/api/dashboards/db",
            {"dashboard": dashboard, "overwrite": True, "message": "repair tenant PGCAT live dashboard"},
            org_id=org_id,
        )
        info("dashboard=%s org=%s action=imported" % (uid, org_id))


def strip_main_org_link(value: Any) -> Any:
    if isinstance(value, str):
        value = value.replace("?orgId=1&", "?")
        value = value.replace("&orgId=1&", "&")
        value = value.replace("?orgId=1", "")
        value = value.replace("&orgId=1", "")
        return value
    if isinstance(value, list):
        return [strip_main_org_link(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_main_org_link(child) for key, child in value.items()}
    return value


def patch_treemap_panels(panels: list[dict[str, Any]]) -> int:
    changes = 0
    for panel in panels:
        if panel.get("type") == "marcusolsson-treemap-panel":
            panel["type"] = "table"
            panel["options"] = {
                "showHeader": True,
                "cellHeight": "sm",
                "footer": {"show": False},
            }
            changes += 1
        changes += patch_treemap_panels(panel.get("panels") or [])
    return changes


def patch_variables(dashboard: dict[str, Any], default_dsn_text: str, default_dsn_uid: str, allowed_dsn_regex: str) -> int:
    changes = 0
    defaults = {
        "dsn": {"text": default_dsn_text, "value": default_dsn_uid},
        "ins": {"text": default_dsn_text.split(".", 1)[0], "value": default_dsn_text.split(".", 1)[0]},
        "datname": {"text": default_dsn_text.split(".", 1)[1], "value": default_dsn_text.split(".", 1)[1]},
        "cls": {"text": "pg-meta", "value": "pg-meta"},
    }
    for var in (dashboard.get("templating") or {}).get("list", []):
        name = var.get("name")
        if name == "dsn":
            # Keep the datasource picker constrained to databases that belong
            # to the tenant namespace. Other tenant PostgreSQL datasources may
            # exist in the org during the raw-PG compatibility window.
            regex = allowed_dsn_regex
            if var.get("regex") != "/" + regex + "/":
                var["regex"] = "/" + regex + "/"
                changes += 1
        if name in defaults:
            current = defaults[name] | {"selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
            if var.get("includeAll"):
                var["includeAll"] = False
                changes += 1
            if var.get("multi"):
                var["multi"] = False
                changes += 1
        if name in {"ins", "datname", "cls", "ip", "node"}:
            # Some links include empty var-ins/var-datname/etc. Empty URL
            # values override computed variable defaults and blank panels.
            if var.get("skipUrlSync") is not True:
                var["skipUrlSync"] = True
                changes += 1
    return changes


def repair_dashboard(
    dashboard: dict[str, Any],
    default_dsn_text: str,
    default_dsn_uid: str,
    allowed_dsn_regex: str,
) -> tuple[dict[str, Any], int]:
    patched = strip_main_org_link(json.loads(json.dumps(dashboard)))
    changes = 0
    changes += patch_treemap_panels(patched.get("panels") or [])
    changes += patch_variables(patched, default_dsn_text, default_dsn_uid, allowed_dsn_regex)
    return patched, changes


def auth_header(args: argparse.Namespace) -> str:
    if args.admin_token:
        return "Bearer " + args.admin_token
    username = args.admin_user or secret_key(args.namespace, args.admin_secret, "username")
    password = args.admin_password or secret_key(args.namespace, args.admin_secret, "password")
    raw = ("%s:%s" % (username, password)).encode()
    return "Basic " + base64.b64encode(raw).decode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair live tenant PGCAT dashboards.")
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "https://grafana.re8ch.com/grafana"))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "qianwen-ops"))
    parser.add_argument("--admin-secret", default=os.environ.get("ADMIN_SECRET", "panel-grafana-admin-recovery"))
    parser.add_argument("--admin-user", default=os.environ.get("GRAFANA_ADMIN_USER", ""))
    parser.add_argument("--admin-password", default=os.environ.get("GRAFANA_ADMIN_PASSWORD", ""))
    parser.add_argument("--admin-token", default=os.environ.get("GRAFANA_ADMIN_TOKEN", ""))
    parser.add_argument("--org-id", type=int, default=int(os.environ.get("GRAFANA_TENANT_ORG_ID", "2")))
    parser.add_argument("--default-dsn-text", default="pg-meta-1.qianwen_prod")
    parser.add_argument("--default-dsn-uid", default="qianwen-pg-meta-1-qianwen-prod")
    parser.add_argument(
        "--allowed-dsn-regex",
        default=r"^pg-meta-[123]\.(qianwen_prod|qianwen_test|cardcat)$",
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    grafana = Grafana(args.grafana_url, auth_header(args))
    for uid in PGCAT_UIDS:
        dashboard = grafana.get_dashboard(args.org_id, uid)
        patched, changes = repair_dashboard(
            dashboard,
            args.default_dsn_text,
            args.default_dsn_uid,
            args.allowed_dsn_regex,
        )
        info("dashboard=%s changes=%d" % (uid, changes))
        if changes:
            grafana.import_dashboard(args.org_id, patched, args.apply)
    info("tenant_pgcat_live_repair=%s" % ("applied" if args.apply else "dry_run"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
