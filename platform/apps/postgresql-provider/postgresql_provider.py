#!/usr/bin/env python3
"""Tenant-isolated PostgreSQL provider for the shared Pigsty authority."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import tenant_control_controller as control

LOG = logging.getLogger("postgresql-provider")
SERVICE_CLASS = "database.postgresql.shared.v1"
PROVIDER_NAME = "postgresql-provider"
PROTECTED_AUTHORITIES = ("pg-identity", "pg-billing", "pg-re8ch")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
EXTENSIONS = {"citext", "pg_trgm", "pgcrypto", "uuid-ossp", "vector"}


def _safe(value: str, limit: int) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "tenant"
    return value[:limit].rstrip("_")


def database_identity(tenant: str, instance: str, uid: str) -> tuple[str, str]:
    if not NAME_RE.fullmatch(tenant) or not NAME_RE.fullmatch(instance) or not uid:
        raise PermissionError("invalid tenant database identity")
    digest = hashlib.sha256(f"{tenant}\0{instance}\0{uid}".encode()).hexdigest()[:10]
    stem = f"t_{_safe(tenant, 18)}_{_safe(instance, 18)}_{digest}"
    database = stem[:63]
    role = (stem + "_owner")[:63]
    if database in {"postgres", "re8ch_identity", "re8ch_billing", "re8ch"}:
        raise PermissionError("tenant database collides with an Admin authority")
    return database, role


def validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    allowed = {"storageGiB", "connectionClass", "backupClass", "retentionClass", "extensions"}
    if set(parameters) - allowed:
        raise PermissionError("PostgreSQL parameters are not published")
    storage = parameters.get("storageGiB")
    if type(storage) is not int or not 1 <= storage <= 1024:
        raise PermissionError("storageGiB must be between 1 and 1024")
    connection = parameters.get("connectionClass", "standard")
    backup = parameters.get("backupClass", "pigsty-pitr")
    retention = parameters.get("retentionClass", "standard")
    if connection not in {"small", "standard", "large"}:
        raise PermissionError("connectionClass is not published")
    if backup not in {"pigsty-pitr"} or retention not in {"standard", "extended"}:
        raise PermissionError("backup or retention class is not published")
    extensions = parameters.get("extensions", [])
    if not isinstance(extensions, list) or len(extensions) > 16 or any(item not in EXTENSIONS for item in extensions):
        raise PermissionError("one or more PostgreSQL extensions are not published")
    return {"storageGiB": storage, "connectionClass": connection, "backupClass": backup,
            "retentionClass": retention, "extensions": sorted(set(extensions))}


class Pigsty:
    def __init__(self, kube: control.Kubernetes):
        secret = kube.get("secrets", "supabase-pg-re8ch-secrets", "supabase-pg-re8ch", "api/v1")
        data = secret.get("data") or {}
        self.user = base64.b64decode(data["POSTGRES_USER_READ_WRITE"]).decode()
        self.password = base64.b64decode(data["POSTGRES_PASSWORD"]).decode()
        self.host = os.getenv("PIGSTY_PRIMARY_HOST", "pg-re8ch-primary.pigsty-external.svc.cluster.local")
        self.port = os.getenv("PIGSTY_PRIMARY_PORT", "5432")

    def connect(self, database: str):
        import psycopg2
        connection = psycopg2.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                                      dbname=database, connect_timeout=10, application_name=PROVIDER_NAME)
        connection.autocommit = True
        return connection

    def ensure(self, database: str, role: str, password: str, extensions: list[str]) -> None:
        from psycopg2 import sql
        with self.connect("postgres") as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)), (password,))
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
                           .format(sql.Identifier(role)), (password,))
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(role)))
            cursor.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(database), sql.Identifier(role)))
        for extension in extensions:
            with self.connect(database) as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))

    def login(self, role: str, enabled: bool) -> None:
        from psycopg2 import sql
        with self.connect("postgres") as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} " + ("LOGIN" if enabled else "NOLOGIN")).format(sql.Identifier(role)))

    def drop(self, database: str, role: str) -> None:
        from psycopg2 import sql
        with self.connect("postgres") as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                           (database,))
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def credential(kube: control.Kubernetes, tenant: str, uid: str, database: str, role: str) -> tuple[str, str]:
    credential_id = hashlib.sha256(f"postgresql\0{tenant}\0{uid}".encode()).hexdigest()[:32]
    name = "postgresql-" + credential_id
    reference = f"credential://postgresql/{tenant}/{credential_id}"
    try:
        current = kube.get("secrets", name, "credential-broker", "api/v1")
        labels = current.get("metadata", {}).get("labels", {})
        if labels.get("credentials.re8ch.com/tenant") != tenant or labels.get("credentials.re8ch.com/provider") != "postgresql":
            raise PermissionError("PostgreSQL credential ownership does not match")
        return reference, base64.b64decode(current["data"]["password"]).decode()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    password = secrets.token_urlsafe(36)
    value = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": "credential-broker",
             "labels": {"credentials.re8ch.com/provider": "postgresql", "credentials.re8ch.com/tenant": tenant,
                        "credentials.re8ch.com/database": database, "credentials.re8ch.com/type": "database-owner"}},
             "type": "Opaque", "stringData": {"username": role, "password": password, "database": database,
             "host": "pg-re8ch-primary.pigsty-external.svc.cluster.local", "port": "5432"}}
    try:
        kube.create("secrets", value, "credential-broker", "api/v1")
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        return credential(kube, tenant, uid, database, role)
    return reference, password


def status(kube: control.Kubernetes, instance: dict[str, Any], phase: str, reason: str, access: dict[str, Any] | None) -> None:
    meta = instance["metadata"]
    kube.patch_status("tenantserviceinstances", meta["name"], {
        "observedGeneration": meta.get("generation", 1), "phase": phase, "reason": None, "access": access,
        "humanCredentialRef": None, "conditions": [control.condition("Ready", phase == "Ready", reason)],
    }, meta["namespace"])


def reconcile(kube: control.Kubernetes, instance: dict[str, Any], pigsty_factory=Pigsty) -> None:
    spec, meta = instance["spec"], instance["metadata"]
    if spec.get("serviceClass") != SERVICE_CLASS:
        return
    tenant, name = spec.get("tenantRef"), meta.get("name")
    if meta.get("labels", {}).get("tenancy.re8ch.com/tenant") != tenant:
        raise PermissionError("service instance tenant label does not match")
    lifecycle = spec.get("lifecycle")
    if tenant == "admin" and name == "postgresql-shared":
        if spec.get("parameters"):
            raise PermissionError("Admin PostgreSQL authority adoption is parameterless")
        service = kube.get("services", "pg-re8ch-primary", "pigsty-external", "api/v1")
        if not (service.get("spec") or {}).get("clusterIP"):
            raise RuntimeError("PostgreSQL primary service authority is not ready")
        phase = "Ready" if lifecycle == "Requested" else ("Suspended" if lifecycle == "Suspended" else "Deleted")
        status(kube, instance, phase, "ExistingPostgreSQLAuthorityReady", {
            "accessRef": "capability://postgresql-admin/admin/pg-re8ch", "authorityRefs": list(PROTECTED_AUTHORITIES),
            "credentialMaterialReturned": False,
        })
        return
    parameters = validate_parameters(spec.get("parameters") or {})
    database, role = database_identity(tenant, name, meta.get("uid"))
    access_ref, password = credential(kube, tenant, meta["uid"], database, role)
    pigsty = pigsty_factory(kube)
    access = {"accessRef": access_ref, "databaseRef": f"pigsty://pg-re8ch/{database}",
              "bindingType": "access:broker-reference", "credentialMaterialReturned": False,
              "allocatedStorageGiB": parameters["storageGiB"], "backupClass": parameters["backupClass"],
              "retentionClass": parameters["retentionClass"]}
    if lifecycle == "Requested":
        pigsty.ensure(database, role, password, parameters["extensions"])
        status(kube, instance, "Ready", "TenantDatabaseReady", access)
    elif lifecycle == "Suspended":
        pigsty.login(role, False)
        status(kube, instance, "Suspended", "TenantDatabaseLoginRevoked", access)
    elif lifecycle == "Deleting":
        pigsty.drop(database, role)
        status(kube, instance, "Deleted", "TenantDatabaseDeleted", access)
    else:
        raise PermissionError("unsupported PostgreSQL provider lifecycle")


def run_once(kube: control.Kubernetes) -> None:
    for item in kube.list("tenantserviceinstances"):
        if item.get("spec", {}).get("serviceClass") != SERVICE_CLASS:
            continue
        try:
            reconcile(kube, item)
        except Exception as exc:
            LOG.exception("PostgreSQL reconcile failed for %s", item.get("metadata", {}).get("name"))
            meta = item["metadata"]
            kube.patch_status("tenantserviceinstances", meta["name"], {
                "observedGeneration": meta.get("generation", 1), "phase": "Failed", "reason": str(exc)[:256],
                "conditions": [control.condition("Ready", False, "ProviderReconcileFailed")],
            }, meta["namespace"])


def register() -> None:
    token = os.getenv("MCP_CATALOG_ADMIN_TOKEN") or os.getenv("DYNAMIC_SKILL_ADMIN_TOKEN", "")
    if not token:
        raise RuntimeError("MCP catalog registration token is unavailable")
    payload = {"action": "apply", "tenantRef": "*", "operationKey": "postgresql-provider-v1", "definition": {
        "serviceName": PROVIDER_NAME, "class": SERVICE_CLASS,
        "endpoint": os.getenv("MCP_ENDPOINT", "http://postgresql-provider.tenant-infrastructure.svc:8080/mcp"),
        "authMode": "oauth-forward", "health": "ready", "shared": True,
        "tools": [{"name": "postgresql_provider_status", "description": "Read the shared Pigsty PostgreSQL provider health and authority boundary.",
                   "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}],
    }}
    request = urllib.request.Request(os.getenv("MCP_CATALOG_ADMIN_URL", "http://tenant-api.tenant-infrastructure.svc:19448/internal/mcp/catalog"),
        data=json.dumps(payload, separators=(",", ":")).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "X-RE8CH-Admin-Subject": "system:postgresql-provider", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("tenant-core rejected PostgreSQL provider registration")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/livez", "/readyz"}:
            self.send_response(200); self.end_headers(); return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_error(404); return
        try:
            request = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 1 << 20)))
            method, rpc_id = request.get("method"), request.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                          "serverInfo": {"name": PROVIDER_NAME, "version": "1.0.0"}}
            elif method == "notifications/initialized":
                self.send_response(202); self.end_headers(); return
            elif method == "tools/list":
                result = {"tools": [{"name": "postgresql_provider_status",
                    "description": "Read the shared Pigsty PostgreSQL provider health and authority boundary.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
            elif method == "tools/call" and (request.get("params") or {}).get("name") == "postgresql_provider_status":
                result = {"content": [{"type": "text", "text": json.dumps({"state": "Ready", "serviceClass": SERVICE_CLASS,
                    "pigstyAuthority": "pg-re8ch", "adminAuthorities": PROTECTED_AUTHORITIES,
                    "credentialMaterialReturned": False}, separators=(",", ":"))}], "isError": False}
            else:
                self._json({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}}); return
            self._json({"jsonrpc": "2.0", "id": rpc_id, "result": result})
        except Exception:
            self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "internal error"}})

    def _json(self, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info(fmt, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    kube = control.Kubernetes()
    register()
    threading.Thread(target=lambda: _loop(kube), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("LISTEN_PORT", "8080"))), Handler).serve_forever()


def _loop(kube: control.Kubernetes) -> None:
    while True:
        run_once(kube)
        time.sleep(15)


if __name__ == "__main__":
    main()
