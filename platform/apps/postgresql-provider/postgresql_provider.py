#!/usr/bin/env python3
"""Tenant-isolated PostgreSQL provider for the shared Pigsty authority."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import tenant_control_controller as control

LOG = logging.getLogger("postgresql-provider")
PROVIDER_NAME = "re8ch-postgresql-provider"
SERVICE_CLASS = "database.postgresql.shared.v1"
CATALOG_RUNTIME_PROFILE = "mcp-catalog-runtime-v1"
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
        import psycopg
        connection = psycopg.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                                     dbname=database, connect_timeout=10, application_name=PROVIDER_NAME)
        connection.autocommit = True
        return connection

    def ensure(self, database: str, role: str, password: str, extensions: list[str]) -> None:
        from psycopg import sql
        with self.connect("postgres") as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)))
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
                           .format(sql.Identifier(role), sql.Literal(password)))
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(role)))
            cursor.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(database), sql.Identifier(role)))
        for extension in extensions:
            with self.connect(database) as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))

    def ensure_catalog_runtime(self, role: str, password: str) -> None:
        """Create the one bounded login used by the dynamic Catalog projector."""
        from psycopg import sql
        with self.connect("re8ch_billing") as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)))
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                   "NOREPLICATION NOINHERIT CONNECTION LIMIT 20")
                           .format(sql.Identifier(role), sql.Literal(password)))
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE re8ch_billing FROM {} CASCADE").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA bifurcate, bifurcate_api FROM {} CASCADE").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA bifurcate FROM {} CASCADE").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bifurcate_api FROM {} CASCADE").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE re8ch_billing TO {}").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA bifurcate, bifurcate_api TO {}").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT SELECT ON bifurcate.active_mcp_catalog TO {}").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT EXECUTE ON FUNCTION bifurcate_api.claim_mcp_catalog_projection_events(integer) TO {}")
                           .format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT EXECUTE ON FUNCTION bifurcate_api.ack_mcp_catalog_projection_event(bigint) TO {}")
                           .format(sql.Identifier(role)))

    def login(self, role: str, enabled: bool) -> None:
        from psycopg import sql
        with self.connect("postgres") as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} " + ("LOGIN" if enabled else "NOLOGIN")).format(sql.Identifier(role)))

    def drop(self, database: str, role: str) -> None:
        from psycopg import sql
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


def catalog_runtime_credential(kube: control.Kubernetes, uid: str, rotation: str) -> tuple[str, str, str]:
    """Prepare Catalog material without publishing it before the database accepts it."""
    name = "postgresql-mcp-catalog-runtime"
    reference = "credential://postgresql/platform/mcp-catalog-runtime"
    try:
        current = kube.get("secrets", name, "credential-broker", "api/v1")
        labels = current.get("metadata", {}).get("labels", {})
        if labels.get("credentials.re8ch.com/tenant") != "platform" or labels.get("credentials.re8ch.com/type") != "catalog-runtime":
            raise PermissionError("Catalog runtime credential ownership does not match")
        password = base64.b64decode(current["data"]["password"]).decode()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        password = secrets.token_urlsafe(36)
        value = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": "credential-broker",
                 "labels": {"credentials.re8ch.com/provider": "postgresql", "credentials.re8ch.com/tenant": "platform",
                            "credentials.re8ch.com/database": "re8ch_billing", "credentials.re8ch.com/type": "catalog-runtime"}},
                 "type": "Opaque", "stringData": {"username": "mcp_catalog_runtime", "password": password,
                 "database": "re8ch_billing", "host": "pg-re8ch-primary.pigsty-external.svc.cluster.local", "port": "5432"}}
        try:
            current = kube.create("secrets", value, "credential-broker", "api/v1")
        except urllib.error.HTTPError as create_exc:
            if create_exc.code != 409:
                raise
            return catalog_runtime_credential(kube, uid, rotation)
    labels = current.get("metadata", {}).get("labels", {})
    if labels.get("credentials.re8ch.com/rotation") != rotation:
        password = secrets.token_urlsafe(36)
    # Consumers assemble or project this broker-held URL server-side; it is never returned by MCP/status.
    database_url = (
        "postgresql://mcp_catalog_runtime:" + urllib.parse.quote(password, safe="") +
        "@pg-re8ch-primary.pigsty-external.svc.cluster.local:5432/re8ch_billing"
    )
    return reference, password, database_url


def project_catalog_runtime_credential(kube: control.Kubernetes, reference: str, password: str,
                                       database_url: str, rotation: str) -> None:
    """Publish the broker credential and its one-key runtime projection after DB commit."""
    name = "postgresql-mcp-catalog-runtime"
    kube.patch("secrets", name, {"metadata": {"labels": {"credentials.re8ch.com/type": "catalog-runtime",
                                                            "credentials.re8ch.com/rotation": rotation}},
                                 "stringData": {"password": password,
                                                "MCP_CATALOG_DATABASE_URL": database_url}},
               "credential-broker", "api/v1")
    kube.patch("secrets", "mcp-catalog-database",
               {"metadata": {"labels": {"credentials.re8ch.com/provider": "postgresql",
                                          "credentials.re8ch.com/type": "catalog-runtime-projection",
                                          "credentials.re8ch.com/rotation": rotation}},
                "stringData": {"MCP_CATALOG_DATABASE_URL": database_url}},
               "tenant-infrastructure", "api/v1")


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
    if tenant == "platform" and name == "mcp-catalog-runtime":
        if spec.get("parameters") != {"profile": CATALOG_RUNTIME_PROFILE}:
            raise PermissionError("Catalog runtime profile is fixed and accepts no privilege input")
        rotation = meta.get("annotations", {}).get("credentials.re8ch.com/rotation")
        if not rotation or len(rotation) > 64:
            raise PermissionError("Catalog runtime requires a bounded reviewed rotation identifier")
        access_ref, password, database_url = catalog_runtime_credential(kube, meta["uid"], rotation)
        pigsty = pigsty_factory(kube)
        access = {"accessRef": access_ref, "databaseRef": "pigsty://pg-re8ch/re8ch_billing",
                  "bindingType": "access:broker-reference", "credentialMaterialReturned": False,
                  "privilegeProfile": CATALOG_RUNTIME_PROFILE}
        if lifecycle == "Requested":
            pigsty.ensure_catalog_runtime("mcp_catalog_runtime", password)
            project_catalog_runtime_credential(kube, access_ref, password, database_url, rotation)
            status(kube, instance, "Ready", "CatalogRuntimeCredentialReady", access)
        elif lifecycle == "Suspended":
            pigsty.login("mcp_catalog_runtime", False)
            status(kube, instance, "Suspended", "CatalogRuntimeLoginRevoked", access)
        else:
            raise PermissionError("Catalog runtime credential may be requested or suspended, not deleted")
        return
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/livez", "/readyz"}:
            self.send_response(200); self.end_headers(); return
        self.send_error(404)

    def do_POST(self) -> None:
        self.send_error(404)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info(fmt, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    kube = control.Kubernetes()
    threading.Thread(target=lambda: _loop(kube), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("LISTEN_PORT", "8080"))), Handler).serve_forever()


def _loop(kube: control.Kubernetes) -> None:
    while True:
        run_once(kube)
        time.sleep(15)


if __name__ == "__main__":
    main()
