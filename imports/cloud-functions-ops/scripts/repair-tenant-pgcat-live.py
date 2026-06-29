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

PG_META_NODE_DEFAULTS = {
    "pg-meta-1": {"ip": "192.0.2.172", "node": "VM-0-5-ubuntu"},
    "pg-meta-2": {"ip": "192.0.2.141", "node": "iZuf6idwzv85k3l3kstj6iZ"},
    "pg-meta-3": {"ip": "192.0.2.33", "node": "iZ7xv81uwok72sii7seok0Z"},
}

PG18_SAFE_PG_STAT_WAL_SQL = """SELECT
  wal_records,
  wal_fpi,
  wal_bytes,
  wal_buffers_full,
  NULL::bigint AS wal_write,
  NULL::bigint AS wal_sync,
  NULL::double precision AS wal_write_time,
  NULL::double precision AS wal_sync_time
FROM pg_stat_wal;"""

EMPTY_WAIT_HISTORY_SQL = """SELECT
  now() AS ts,
  NULL::integer AS pid,
  'N/A'::text AS event_type,
  'pg_wait_sampling is not installed or has no recent samples'::text AS event;"""

EMPTY_WAIT_PROFILE_SQL = """SELECT
  'N/A'::text AS event_type,
  'pg_wait_sampling is not installed or has no recent samples'::text AS event,
  0::bigint AS count;"""

EMPTY_WAIT_COUNT_SQL = """SELECT
  'N/A'::text AS event_type,
  'pg_wait_sampling is not installed or has no recent samples'::text AS event,
  0::bigint AS count;"""

EMPTY_WAIT_PIE_SQL = """SELECT
  now() AS time,
  'no wait sampling'::text AS metric,
  0::bigint AS value;"""

SUBSCRIPTION_COUNT_SQL = """SELECT
  count(*)::bigint AS subscriptions
FROM pg_stat_subscription;"""

WAL_RECEIVER_COUNT_SQL = """SELECT
  count(*)::bigint AS wal_receivers
FROM pg_stat_wal_receiver;"""

LOGICAL_SLOT_COUNT_SQL = """SELECT
  count(*)::bigint AS logical_replication_slots
FROM pg_replication_slots
WHERE slot_type = 'logical';"""

REL_OPTIONS_SQL = """SELECT unnest(reloptions) AS opt
FROM pg_class c
WHERE oid = '${oid}'
UNION ALL
SELECT 'no reloptions configured'::text AS opt
WHERE NOT EXISTS (
  SELECT 1
  FROM pg_class c
  WHERE oid = '${oid}' AND reloptions IS NOT NULL
);"""

TENANT_DATABASES = ("qianwen_prod", "qianwen_test", "cardcat")
TENANT_DATABASE_SQL = ", ".join("'%s'" % database for database in TENANT_DATABASES)
TENANT_ACTIVITY_SCOPE_SQL = "(datname IS NULL OR datname IN (%s))" % TENANT_DATABASE_SQL
TENANT_ROLE_SCOPE_SQL = (
    "(r.rolname ~ '^(qianwen_prod|qianwen_test|cardcat)($|_)' "
    "OR r.rolname = 'dbuser_monitor')"
)
TENANT_MEMBER_ROLE_SCOPE_SQL = (
    "(b.rolname ~ '^(qianwen_prod|qianwen_test|cardcat)($|_)' "
    "OR b.rolname IN ('dbuser_monitor', 'web_user', 'web_anon'))"
)
TENANT_LOCK_SQL = """WITH locked AS (
  SELECT
    pid,
    datname,
    usename,
    state,
    wait_event_type,
    wait_event,
    pg_blocking_pids(pid)::text AS blocked_by,
    left(query, 240) AS query
  FROM pg_stat_activity
  WHERE datname IN (%s)
    AND cardinality(pg_blocking_pids(pid)) > 0
)
SELECT * FROM locked
UNION ALL
SELECT
  NULL::integer AS pid,
  '${datname}'::name AS datname,
  NULL::name AS usename,
  'idle'::text AS state,
  'N/A'::text AS wait_event_type,
  'No active lock waits for tenant databases'::text AS wait_event,
  '{}'::text AS blocked_by,
  NULL::text AS query
WHERE NOT EXISTS (SELECT 1 FROM locked);""" % TENANT_DATABASE_SQL

DATABASE_LOCK_SQL = """WITH locked AS (
  SELECT
    pid,
    datname,
    usename,
    state,
    wait_event_type,
    wait_event,
    pg_blocking_pids(pid)::text AS blocked_by,
    left(query, 240) AS query
  FROM pg_stat_activity
  WHERE datname = '${datname}'
    AND cardinality(pg_blocking_pids(pid)) > 0
)
SELECT * FROM locked
UNION ALL
SELECT
  NULL::integer AS pid,
  '${datname}'::name AS datname,
  NULL::name AS usename,
  'idle'::text AS state,
  'N/A'::text AS wait_event_type,
  'No active lock waits for ${datname}'::text AS wait_event,
  '{}'::text AS blocked_by,
  NULL::text AS query
WHERE NOT EXISTS (SELECT 1 FROM locked);"""


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


def patch_missing_plugin_panels(panels: list[dict[str, Any]]) -> int:
    changes = 0
    for panel in panels:
        if panel.get("type") in {"marcusolsson-treemap-panel", "marcusolsson-dynamictext-panel"}:
            panel["type"] = "table"
            panel["options"] = {
                "showHeader": True,
                "cellHeight": "sm",
                "footer": {"show": False},
            }
            changes += 1
        changes += patch_missing_plugin_panels(panel.get("panels") or [])
    return changes


def patch_variables(dashboard: dict[str, Any], default_dsn_text: str, default_dsn_uid: str, allowed_dsn_regex: str) -> int:
    changes = 0
    default_ins = default_dsn_text.split(".", 1)[0]
    node_defaults = PG_META_NODE_DEFAULTS.get(default_ins, {})
    defaults = {
        "dsn": {"text": default_dsn_text, "value": default_dsn_uid},
        "ins": {"text": default_ins, "value": default_ins},
        "datname": {"text": default_dsn_text.split(".", 1)[1], "value": default_dsn_text.split(".", 1)[1]},
        "cls": {"text": "pg-meta", "value": "pg-meta"},
        "ip": {"text": node_defaults.get("ip", ""), "value": node_defaults.get("ip", "")},
        "node": {"text": node_defaults.get("node", ""), "value": node_defaults.get("node", "")},
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
        if name in {"ip", "node"}:
            wanted_query = defaults[name]["value"]
            if var.get("type") != "custom":
                var["type"] = "custom"
                changes += 1
            if var.get("query") != wanted_query:
                var["query"] = wanted_query
                changes += 1
            if var.get("hide") != 2:
                var["hide"] = 2
                changes += 1
        if name == "query":
            current = {
                "text": os.environ.get("PGCAT_DEFAULT_QUERY_ID", "-3704990386267960320"),
                "value": os.environ.get("PGCAT_DEFAULT_QUERY_ID", "-3704990386267960320"),
                "selected": True,
            }
            if var.get("current") != current:
                var["current"] = current
                changes += 1
        if name == "schema":
            current = {"text": "public", "value": "public", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
        if name == "nspid":
            current = {"text": "2200", "value": "2200", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
            if var.get("skipUrlSync") is not True:
                var["skipUrlSync"] = True
                changes += 1
        if name == "relname":
            current = {"text": "public.t_bg_task", "value": "public.t_bg_task", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
        if name == "nspname":
            current = {"text": "public", "value": "public", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
            if var.get("skipUrlSync") is not True:
                var["skipUrlSync"] = True
                changes += 1
        if name == "tblname":
            current = {"text": "t_bg_task", "value": "t_bg_task", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
            if var.get("skipUrlSync") is not True:
                var["skipUrlSync"] = True
                changes += 1
        if name == "oid":
            current = {"text": "24988", "value": "24988", "selected": True}
            if var.get("current") != current:
                var["current"] = current
                changes += 1
            if var.get("skipUrlSync") is not True:
                var["skipUrlSync"] = True
                changes += 1
    return changes


def patch_pg18_stat_wal(panels: list[dict[str, Any]]) -> int:
    changes = 0
    for panel in panels:
        for target in panel.get("targets") or []:
            raw_sql = target.get("rawSql")
            if not isinstance(raw_sql, str):
                continue
            if "FROM pg_stat_wal" in raw_sql and "wal_write" in raw_sql:
                if raw_sql != PG18_SAFE_PG_STAT_WAL_SQL:
                    target["rawSql"] = PG18_SAFE_PG_STAT_WAL_SQL
                    changes += 1
        changes += patch_pg18_stat_wal(panel.get("panels") or [])
    return changes


def patch_locks_wait_sampling(panels: list[dict[str, Any]]) -> int:
    changes = 0
    replacements = {
        223: EMPTY_WAIT_HISTORY_SQL,
        222: EMPTY_WAIT_PROFILE_SQL,
        224: EMPTY_WAIT_COUNT_SQL,
        228: EMPTY_WAIT_PIE_SQL,
    }
    for panel in panels:
        for target in panel.get("targets") or []:
            raw_sql = target.get("rawSql")
            if not isinstance(raw_sql, str):
                continue
            safe_sql = replacements.get(panel.get("id"))
            if safe_sql and raw_sql != safe_sql:
                target["rawSql"] = safe_sql
                changes += 1
        changes += patch_locks_wait_sampling(panel.get("panels") or [])
    return changes


def patch_optional_pgcat_panels(panels: list[dict[str, Any]]) -> int:
    changes = 0
    sql_by_id = {
        35: REL_OPTIONS_SQL,
        144: LOGICAL_SLOT_COUNT_SQL,
        146: WAL_RECEIVER_COUNT_SQL,
        147: SUBSCRIPTION_COUNT_SQL,
        179: TENANT_LOCK_SQL,
        218: DATABASE_LOCK_SQL,
        220: DATABASE_LOCK_SQL,
    }
    promql_by_id = {
        225: 'sum by (event) (rate(pg_wait_event_count{ins="$ins"}[30s])) or on() vector(0)',
    }
    for panel in panels:
        for target in panel.get("targets") or []:
            raw_sql = target.get("rawSql")
            expr = target.get("expr")
            wanted_sql = sql_by_id.get(panel.get("id"))
            if wanted_sql and isinstance(raw_sql, str) and raw_sql != wanted_sql:
                target["rawSql"] = wanted_sql
                changes += 1
            wanted_expr = promql_by_id.get(panel.get("id"))
            if wanted_expr and isinstance(expr, str) and expr != wanted_expr:
                target["expr"] = wanted_expr
                changes += 1
        changes += patch_optional_pgcat_panels(panel.get("panels") or [])
    return changes


def patch_query_dashboard(panels: list[dict[str, Any]]) -> int:
    changes = 0
    query_summary_sql = (
        "SELECT queryid::TEXT AS qid, d.datname, r.rolname, "
        "s.calls, s.total_exec_time, s.mean_exec_time, s.rows, s.wal_bytes, "
        "('[redacted queryid=' || s.queryid::text || ']')::text AS query "
        "FROM pg_stat_statements s "
        "LEFT JOIN pg_database d ON s.dbid = d.oid "
        "LEFT JOIN pg_roles r ON s.userid = r.oid "
        "WHERE d.datname = '${datname}' "
        "AND s.queryid = nullif('$query','')::bigint "
        "LIMIT 1;"
    )
    top_sql = (
        "SELECT queryid::TEXT AS qid, d.datname, r.rolname, "
        "s.userid, s.dbid, s.toplevel, s.queryid, "
        "('[redacted queryid=' || s.queryid::text || ']')::text AS query, "
        "s.plans, s.total_plan_time, s.min_plan_time, s.max_plan_time, s.mean_plan_time, s.stddev_plan_time, "
        "s.calls, s.total_exec_time, s.min_exec_time, s.max_exec_time, s.mean_exec_time, s.stddev_exec_time, s.rows, "
        "s.shared_blks_hit, s.shared_blks_read, s.shared_blks_dirtied, s.shared_blks_written, "
        "s.local_blks_hit, s.local_blks_read, s.local_blks_dirtied, s.local_blks_written, "
        "s.temp_blks_read, s.temp_blks_written, "
        "s.shared_blk_read_time, s.shared_blk_write_time, s.local_blk_read_time, s.local_blk_write_time, "
        "s.temp_blk_read_time, s.temp_blk_write_time, "
        "s.wal_records, s.wal_fpi, s.wal_bytes, s.wal_buffers_full, "
        "s.jit_functions, s.jit_generation_time, s.jit_inlining_count, s.jit_inlining_time, "
        "s.jit_optimization_count, s.jit_optimization_time, s.jit_emission_count, s.jit_emission_time, "
        "s.jit_deform_count, s.jit_deform_time, "
        "s.parallel_workers_to_launch, s.parallel_workers_launched, "
        "s.stats_since, s.minmax_stats_since "
        "FROM pg_stat_statements s "
        "LEFT JOIN pg_database d ON s.dbid = d.oid "
        "LEFT JOIN pg_roles r ON s.userid = r.oid "
        "WHERE d.datname='${datname}' ORDER BY mean_exec_time DESC LIMIT 100;"
    )
    for panel in panels:
        for target in panel.get("targets") or []:
            raw_sql = target.get("rawSql")
            if not isinstance(raw_sql, str):
                continue
            replacement = None
            if panel.get("id") == 89:
                replacement = query_summary_sql
            elif panel.get("id") == 90:
                replacement = top_sql
            if replacement and raw_sql != replacement:
                target["rawSql"] = replacement
                changes += 1
        changes += patch_query_dashboard(panel.get("panels") or [])
    return changes


def patch_instance_tenant_scope(panels: list[dict[str, Any]]) -> int:
    changes = 0
    panel_sql_by_id = {
        180: (
            "SELECT * FROM "
            "(SELECT count(*) AS databases FROM pg_database WHERE datname IN (%s)) d, "
            "(SELECT count(*) AS roles FROM pg_roles r WHERE %s) r, "
            "(SELECT count(*) AS tablespaces FROM pg_tablespace) tsp;"
            % (TENANT_DATABASE_SQL, TENANT_ROLE_SCOPE_SQL)
        ),
        57: (
            "SELECT d.datname, d.oid::INTEGER, d.datconnlimit, d.datfrozenxid, age(d.datfrozenxid) AS age, "
            "datistemplate, datallowconn, "
            "1 - (datistemplate)::INTEGER + 2 * d.datallowconn::INTEGER AS status, "
            "psd.numbackends AS sessions, "
            "pg_catalog.pg_get_userbyid(d.datdba) as owner, pg_catalog.pg_encoding_to_char(d.encoding) as encoding, "
            "d.datcollate, d.datctype, pg_catalog.array_to_string(d.datacl, E'\\n') AS acl, "
            "CASE WHEN pg_catalog.has_database_privilege(d.datname, 'CONNECT') THEN pg_catalog.pg_database_size(d.datname) ELSE -1 END AS size, "
            "t.spcname as tablespace, pg_catalog.shobj_description(d.oid, 'pg_database') as description "
            "FROM pg_catalog.pg_database d "
            "JOIN pg_catalog.pg_tablespace t on d.dattablespace = t.oid "
            "JOIN pg_stat_database psd on d.oid = psd.datid "
            "WHERE d.datname IN (%s) ORDER BY 1;" % TENANT_DATABASE_SQL
        ),
        59: (
            "SELECT pg_blocking_pids(pid) AS blocking_pids, datid, datname, pid, leader_pid, usesysid, usename, "
            "application_name, client_addr, client_hostname, client_port, "
            "extract(epoch from now() - backend_start) AS conn_time, "
            "extract(epoch from now() - xact_start) AS xact_time, "
            "extract(epoch from now() - query_start) AS query_time, "
            "extract(epoch from now() - state_change) AS state_time, "
            "wait_event_type, wait_event, state, backend_xid, backend_xmin, query_id, query, backend_type, "
            "query_id::TEXT AS qid "
            "FROM pg_stat_activity WHERE %s;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        105: (
            "SELECT extract(epoch from now()) AS time, coalesce(backend_type, 'null') as metric, count(*) as value "
            "FROM pg_stat_activity WHERE %s GROUP BY coalesce(backend_type, 'null');" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        104: (
            "SELECT extract(epoch from now()) AS time, coalesce(state, '_system') AS metric, count(*) AS value "
            "FROM pg_stat_activity WHERE %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        103: (
            "SELECT extract(epoch from now()) AS time, coalesce(wait_event, 'Running') as metric, count(*) as value "
            "FROM pg_stat_activity WHERE %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        107: (
            "SELECT now() AS time, metric AS metric, value FROM ("
            "SELECT coalesce(host(client_addr),'') || CASE client_port WHEN -1 THEN 'unix socket' ELSE ':' || client_port::TEXT END AS metric, count(*) AS value "
            "FROM pg_stat_activity WHERE state IS NOT NULL AND %s GROUP BY 1) s;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        109: (
            "SELECT extract(epoch from now()) AS time, coalesce(usename, '_system') as metric, count(*) as value "
            "FROM pg_stat_activity WHERE state IS NOT NULL AND %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        106: (
            "SELECT extract(epoch from now()) AS time, CASE application_name WHEN '' THEN 'unknown' ELSE application_name END as metric, count(*) as value "
            "FROM pg_stat_activity WHERE state IS NOT NULL AND %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL
        ),
        172: (
            "SELECT queryid::TEXT AS qid, d.datname, r.rolname, "
            "s.userid, s.dbid, s.toplevel, s.queryid, "
            "('[redacted queryid=' || s.queryid::text || ']')::text AS query, "
            "s.plans, s.total_plan_time, s.min_plan_time, s.max_plan_time, s.mean_plan_time, s.stddev_plan_time, "
            "s.calls, s.total_exec_time, s.min_exec_time, s.max_exec_time, s.mean_exec_time, s.stddev_exec_time, s.rows, "
            "s.shared_blks_hit, s.shared_blks_read, s.shared_blks_dirtied, s.shared_blks_written, "
            "s.local_blks_hit, s.local_blks_read, s.local_blks_dirtied, s.local_blks_written, "
            "s.temp_blks_read, s.temp_blks_written, "
            "s.shared_blk_read_time, s.shared_blk_write_time, s.local_blk_read_time, s.local_blk_write_time, "
            "s.temp_blk_read_time, s.temp_blk_write_time, "
            "s.wal_records, s.wal_fpi, s.wal_bytes, s.wal_buffers_full, "
            "s.jit_functions, s.jit_generation_time, s.jit_inlining_count, s.jit_inlining_time, "
            "s.jit_optimization_count, s.jit_optimization_time, s.jit_emission_count, s.jit_emission_time, "
            "s.jit_deform_count, s.jit_deform_time, "
            "s.parallel_workers_to_launch, s.parallel_workers_launched, "
            "s.stats_since, s.minmax_stats_since "
            "FROM pg_stat_statements s "
            "LEFT JOIN pg_database d ON s.dbid = d.oid "
            "LEFT JOIN pg_roles r ON s.userid = r.oid "
            "WHERE d.datname IN (%s) ORDER BY total_exec_time DESC LIMIT 100;" % TENANT_DATABASE_SQL
        ),
        84: (
            "SELECT r.oid, r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb, r.rolcanlogin, "
            "r.rolconnlimit, "
            "ARRAY(SELECT b.rolname FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles b ON (m.roleid = b.oid) WHERE m.member = r.oid AND %s) as memberof, "
            "pg_catalog.shobj_description(r.oid, 'pg_authid') AS description, r.rolreplication, r.rolbypassrls "
            "FROM pg_catalog.pg_roles r WHERE r.rolname !~ '^pg_' AND %s ORDER BY 1;"
            % (TENANT_MEMBER_ROLE_SCOPE_SQL, TENANT_ROLE_SCOPE_SQL)
        ),
    }
    replacements = {
        "SELECT * FROM (SELECT count(*) AS databases FROM pg_database) d,      (SELECT count(*) AS roles FROM pg_roles) r,      (SELECT count(*) AS tablespaces FROM pg_tablespace) tsp     -- (SELECT count(*) AS session FROM pg_stat_activity) proc,     -- (SELECT current_setting('max_connections')::INTEGER AS maxconn) mc ;":
            "SELECT * FROM "
            "(SELECT count(*) AS databases FROM pg_database WHERE datname IN (%s)) d, "
            "(SELECT count(*) AS roles FROM pg_roles r WHERE %s) r, "
            "(SELECT count(*) AS tablespaces FROM pg_tablespace) tsp;"
            % (TENANT_DATABASE_SQL, TENANT_ROLE_SCOPE_SQL),
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_tablespace t on d.dattablespace = t.oid JOIN pg_stat_database psd on d.oid = psd.datid ORDER BY 1;":
            "FROM pg_catalog.pg_database d "
            "JOIN pg_catalog.pg_tablespace t on d.dattablespace = t.oid "
            "JOIN pg_stat_database psd on d.oid = psd.datid "
            "WHERE d.datname IN (%s) ORDER BY 1;" % TENANT_DATABASE_SQL,
        "FROM pg_stat_activity;":
            "FROM pg_stat_activity WHERE %s;" % TENANT_ACTIVITY_SCOPE_SQL,
        "FROM pg_stat_activity GROUP BY coalesce(backend_type, 'null');":
            "FROM pg_stat_activity WHERE %s GROUP BY coalesce(backend_type, 'null');" % TENANT_ACTIVITY_SCOPE_SQL,
        "FROM pg_stat_activity -- WHERE state IS NOT NULL GROUP BY 2;":
            "FROM pg_stat_activity WHERE %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL,
        "FROM pg_stat_activity GROUP BY 2;":
            "FROM pg_stat_activity WHERE %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL,
        "FROM pg_stat_activity WHERE state IS NOT NULL GROUP BY 1);":
            "FROM pg_stat_activity WHERE state IS NOT NULL AND %s GROUP BY 1);" % TENANT_ACTIVITY_SCOPE_SQL,
        "FROM pg_stat_activity WHERE state IS NOT NULL GROUP BY 2;":
            "FROM pg_stat_activity WHERE state IS NOT NULL AND %s GROUP BY 2;" % TENANT_ACTIVITY_SCOPE_SQL,
        "LEFT JOIN pg_roles r ON s.userid = r.oid ORDER BY total_exec_time DESC LIMIT 100;":
            "LEFT JOIN pg_roles r ON s.userid = r.oid "
            "WHERE d.datname IN (%s) ORDER BY total_exec_time DESC LIMIT 100;" % TENANT_DATABASE_SQL,
        "FROM pg_catalog.pg_roles r WHERE r.rolname !~ '^pg_' ORDER BY 1;":
            "FROM pg_catalog.pg_roles r WHERE r.rolname !~ '^pg_' AND %s ORDER BY 1;" % TENANT_ROLE_SCOPE_SQL,
    }
    for panel in panels:
        for target in panel.get("targets") or []:
            raw_sql = target.get("rawSql")
            if not isinstance(raw_sql, str):
                continue
            patched_sql = panel_sql_by_id.get(panel.get("id"), raw_sql)
            for before, after in replacements.items():
                patched_sql = patched_sql.replace(before, after)
            if patched_sql != raw_sql:
                target["rawSql"] = patched_sql
                changes += 1
        changes += patch_instance_tenant_scope(panel.get("panels") or [])
    return changes


def repair_dashboard(
    dashboard: dict[str, Any],
    default_dsn_text: str,
    default_dsn_uid: str,
    allowed_dsn_regex: str,
) -> tuple[dict[str, Any], int]:
    patched = strip_main_org_link(json.loads(json.dumps(dashboard)))
    changes = 0
    changes += patch_missing_plugin_panels(patched.get("panels") or [])
    changes += patch_variables(patched, default_dsn_text, default_dsn_uid, allowed_dsn_regex)
    changes += patch_pg18_stat_wal(patched.get("panels") or [])
    if patched.get("uid") == "tenant-qianwen-pgcat-instance":
        changes += patch_instance_tenant_scope(patched.get("panels") or [])
    if patched.get("uid") == "tenant-qianwen-pgcat-locks":
        changes += patch_locks_wait_sampling(patched.get("panels") or [])
    if patched.get("uid") == "tenant-qianwen-pgcat-query":
        changes += patch_query_dashboard(patched.get("panels") or [])
    changes += patch_optional_pgcat_panels(patched.get("panels") or [])
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
