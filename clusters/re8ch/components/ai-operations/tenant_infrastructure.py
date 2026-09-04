#!/usr/bin/env python3
"""Tenant-scoped infrastructure service catalog and durable lifecycle store."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATES = {"Pending", "Provisioning", "Ready", "Suspended", "Deleting", "Deleted", "Failed"}
TENANT_STATES = {"Active", "Deleting", "Deleted"}
VISIBILITIES = {"Private", "Tenant", "Shared"}
FORBIDDEN = ("password", "token", "secret", "kubeconfig", "privatekey", "private_key", "credential")

CATALOG: dict[str, dict[str, Any]] = {
    "database.postgresql.shared.v1": {
        "plans": ["standard"], "regions": ["loudi"],
        "parameters": {"storageGiB", "connectionClass", "backupClass", "retentionClass", "extensions"},
        "meters": {"instance_hours": "hour", "allocated_storage": "GiB-hour", "backup_storage": "GiB-month", "connection_class": "class-hour"},
        "adapter": "pigsty-postgresql", "accessUnit": "DatabaseRoleSchema", "bindings": ["endpoint:rw", "endpoint:ro", "access:broker-reference"],
    },
    "storage.volume.v1": {
        "plans": ["rbd", "cephfs"], "regions": ["loudi"],
        "parameters": {"sizeGiB", "accessMode", "snapshotClass", "backupClass"},
        "meters": {"provisioned_storage": "GiB-hour", "snapshot_storage": "GiB-month", "read_bytes": "byte", "write_bytes": "byte"},
        "adapter": "rook-ceph-csi", "accessUnit": "PersistentVolumeClaim", "bindings": ["pvc:{tenant}/{name}"],
    },
    "storage.bucket.v1": {
        "plans": ["dedicated", "shared-prefix"], "regions": ["guangzhou", "hangzhou", "hongkong", "shanghai"],
        "parameters": {"quotaGiB", "visibility", "lifecycleDays", "provider"},
        "meters": {"stored_data": "GiB-hour", "requests": "request", "ingress_bytes": "byte", "egress_bytes": "byte"},
        "adapter": "rook-ceph-rgw", "accessUnit": "ObjectBucketClaim", "bindings": ["bucket:{tenant}/{name}", "access:broker-reference"],
    },
    "network.tenant.v1": {
        "plans": ["public", "full"], "regions": ["global", "cn", "apac", "us"],
        "parameters": {"features", "hostname", "serviceRef", "ports", "protocol", "bandwidthClass", "dnsZoneRef"},
        "meters": {"public_ip": "IP-hour", "bandwidth_class": "class-hour", "ingress_bytes": "byte", "egress_bytes": "byte", "dns_queries": "query", "tunnel_connections": "connection-hour"},
        "adapter": "cilium-public-edge", "accessUnit": "NamespaceIdentityAndRoute", "bindings": ["policy:default-deny", "route:tenant-scoped"],
    },
    "network.among-clusters.v1": {
        "plans": ["private", "metered-egress"], "regions": ["global", "cn", "apac", "us"],
        "parameters": {"sourceClusterRef", "destinationClusterRefs", "namespaceRef", "serviceRefs", "bandwidthClass"},
        "meters": {"path_hours": "path-hour", "ingress_bytes": "byte", "egress_bytes": "byte", "route_changes": "change"},
        "adapter": "among-clusters-access", "accessUnit": "AuthenticatedClusterLink", "bindings": ["link:authenticated", "grant:service-import"],
    },
    "registry.harbor.v1": {
        "plans": ["pull", "build-push", "project-admin"], "regions": ["global", "loudi"],
        "parameters": {"project", "quotaGiB", "retentionDays", "robotDurationDays", "scanPolicy"},
        "meters": {"artifact_storage": "GiB-hour", "pull_bytes": "byte", "push_bytes": "byte", "scans": "scan"},
        "adapter": "harbor-project-rbac", "accessUnit": "ProjectMembershipRobot", "bindings": ["project:membership", "access:broker-reference"],
    },
    "observability.grafana.v1": {
        "plans": ["viewer", "editor"], "regions": ["global", "loudi"],
        "parameters": {"organizationRef", "folderRefs", "datasourceRefs", "retentionClass"},
        "meters": {"active_series": "series-hour", "samples": "sample", "logs": "byte", "retained_data": "GiB-hour", "queries": "query"},
        "adapter": "grafana-access", "accessUnit": "OrganizationFolderDatasourceGrant", "bindings": ["oidc:team", "grafana:folder-grant", "grafana:datasource-grant"],
    },
    "ai.model-gateway.v1": {
        "plans": ["standard", "budgeted"], "regions": ["global", "cn"],
        "parameters": {"models", "tokenBudget", "concurrency", "expiresAt"},
        "meters": {"input_tokens": "token", "output_tokens": "token", "requests": "request", "gpu_time": "GPU-second"},
        "adapter": "litellm-team-policy", "accessUnit": "VirtualKeyTeamModelPolicy", "bindings": ["access:broker-reference", "policy:model-allowlist"],
    },
    "sandbox.runtime.v1": {
        "plans": ["ephemeral", "artifact-retained"], "regions": ["loudi"],
        "parameters": {"imageDigest", "cpu", "memory", "storageGiB", "ttlSeconds", "networkClass", "retentionClass"},
        "meters": {"runtime": "second", "cpu": "core-second", "memory": "GiB-second", "artifacts": "GiB-hour", "egress": "byte"},
        "adapter": "opensandbox-session", "accessUnit": "SandboxSession", "bindings": ["sandbox:session", "artifact:scoped"],
    },
}


class ServiceError(ValueError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def catalog() -> list[dict[str, Any]]:
    return [{"serviceClass": name, **{key: sorted(item) if isinstance(item, set) else item for key, item in value.items()}} for name, value in sorted(CATALOG.items())]


def validate_spec(spec: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed = {"serviceClass", "planRef", "regionRef", "desiredState", "parameters", "labels"}
    unknown = set(spec) - allowed
    if unknown:
        raise ServiceError("unknown service fields: " + ", ".join(sorted(unknown)))
    service_class = str(spec.get("serviceClass") or "")
    product = CATALOG.get(service_class)
    if not product:
        raise ServiceError("serviceClass is not published")
    if spec.get("planRef") not in product["plans"]:
        raise ServiceError("planRef is not published for serviceClass")
    if spec.get("regionRef") not in product["regions"]:
        raise ServiceError("regionRef is not published for serviceClass")
    if spec.get("desiredState", "Ready") not in {"Ready", "Suspended", "Deleted"}:
        raise ServiceError("invalid desiredState")
    parameters = spec.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ServiceError("parameters must be an object")
    forbidden = [key for key in parameters if any(x in key.lower() for x in FORBIDDEN)]
    if forbidden:
        raise ServiceError("credential material is forbidden")
    extra = set(parameters) - product["parameters"]
    if extra:
        raise ServiceError("unknown parameters: " + ", ".join(sorted(extra)))
    if service_class == "storage.volume.v1":
        size = int(parameters.get("sizeGiB", 0))
        if size < 1:
            raise ServiceError("sizeGiB must be positive")
        if previous and size < int(previous.get("parameters", {}).get("sizeGiB", 0)):
            raise ServiceError("volume shrink is not supported")
    if service_class == "storage.bucket.v1" and parameters.get("visibility", "private") != "private":
        raise ServiceError("public buckets require a separate network publication binding")
    if service_class == "network.tenant.v1" and "candidateRef" in parameters:
        raise ServiceError("PublicEdge candidate selection is platform-owned")
    if service_class == "registry.harbor.v1":
        if not parameters.get("project") or int(parameters.get("quotaGiB", 0)) < 1 or not 0 < int(parameters.get("robotDurationDays", 0)) <= 365:
            raise ServiceError("Harbor requires project, positive quotaGiB and bounded robotDurationDays")
    if service_class == "observability.grafana.v1":
        if not parameters.get("datasourceRefs") or not (parameters.get("organizationRef") or parameters.get("folderRefs")):
            raise ServiceError("Grafana requires datasourceRefs and an organizationRef or folderRefs")
    if service_class == "network.among-clusters.v1" and not parameters.get("destinationClusterRefs"):
        raise ServiceError("AmongClusters requires destinationClusterRefs")
    return {**spec, "desiredState": spec.get("desiredState", "Ready"), "parameters": parameters, "labels": spec.get("labels") or {}}


def provisioning_intent(tenant: str, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    resource_ref = f"service://{tenant}/{name}"
    service_class = spec["serviceClass"]
    product = CATALOG[service_class]
    bindings = [value.format(tenant=tenant, name=name) for value in product["bindings"]]
    return {"adapter": product["adapter"], "accessUnit": product["accessUnit"], "bindings": bindings, "resourceRef": resource_ref, "tenantRef": tenant, "desiredState": spec["desiredState"], "credentialMaterialReturned": False}


class ServiceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.lock = path, threading.Lock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS instances(resource_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, name TEXT NOT NULL, service_class TEXT NOT NULL, state TEXT NOT NULL, spec_json TEXT NOT NULL, generation INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(tenant_ref,name));
            CREATE TABLE IF NOT EXISTS operations(operation_id TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, action TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, before_json TEXT, after_json TEXT, result_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tenant_ref,idempotency_key));
            CREATE TABLE IF NOT EXISTS bindings(binding_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, binding_type TEXT NOT NULL, binding_json TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_outbox(event_id TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, event_json TEXT NOT NULL, delivered_at TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tenants(tenant_ref TEXT PRIMARY KEY, display_name TEXT NOT NULL, visibility TEXT NOT NULL, state TEXT NOT NULL, identity_ref TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS grants(grant_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, consumable_ref TEXT NOT NULL, role_ref TEXT NOT NULL, visibility TEXT NOT NULL, constraints_json TEXT NOT NULL, access_ref TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT, UNIQUE(tenant_ref,consumable_ref,role_ref));
            CREATE TABLE IF NOT EXISTS cluster_registrations(registration_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, cluster_uid TEXT NOT NULL, region_ref TEXT NOT NULL, issuer TEXT NOT NULL, public_key_hash TEXT NOT NULL, capabilities_json TEXT NOT NULL, state TEXT NOT NULL, enrollment_ref TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(tenant_ref,cluster_uid));
            CREATE TABLE IF NOT EXISTS cluster_connections(connection_ref TEXT PRIMARY KEY, registration_ref TEXT NOT NULL UNIQUE, tenant_ref TEXT NOT NULL, state TEXT NOT NULL, identity_ref TEXT NOT NULL, last_heartbeat TEXT, revoked_at TEXT, created_at TEXT NOT NULL);
            """)

    @staticmethod
    def validate_tenant_ref(tenant_ref: str) -> str:
        value = tenant_ref.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value):
            raise ServiceError("tenantRef must be a DNS label")
        return value

    def create_tenant(self, tenant_ref: str, display_name: str, identity_ref: str) -> dict[str, Any]:
        tenant_ref = self.validate_tenant_ref(tenant_ref)
        if tenant_ref == "admin":
            raise ServiceError("admin is protected and cannot be created through the tenant API")
        if not display_name.strip() or not identity_ref.strip():
            raise ServiceError("displayName and identityRef are required")
        now = timestamp()
        with self.connect() as db:
            try:
                db.execute("INSERT INTO tenants VALUES(?,?,?,?,?,?,?,?)", (tenant_ref, display_name.strip(), "Private", "Active", identity_ref.strip(), now, now, None))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ServiceError("tenant already exists") from exc
        return {"tenantRef": tenant_ref, "displayName": display_name.strip(), "identityRef": identity_ref.strip(), "visibility": "Private", "state": "Active"}

    def tenants(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tenants ORDER BY tenant_ref").fetchall()
        return [dict(row) for row in rows]

    def set_visibility(self, tenant_ref: str, visibility: str) -> dict[str, Any]:
        tenant_ref = self.validate_tenant_ref(tenant_ref)
        if tenant_ref == "admin":
            raise ServiceError("admin visibility is protected")
        if visibility not in VISIBILITIES:
            raise ServiceError("visibility must be Private, Tenant or Shared")
        now = timestamp()
        with self.connect() as db:
            changed = db.execute("UPDATE tenants SET visibility=?,updated_at=? WHERE tenant_ref=? AND state='Active'", (visibility, now, tenant_ref)).rowcount
            db.commit()
        if not changed:
            raise ServiceError("active tenant not found")
        return {"tenantRef": tenant_ref, "visibility": visibility, "updatedAt": now}

    def delete_tenant(self, tenant_ref: str) -> dict[str, Any]:
        tenant_ref = self.validate_tenant_ref(tenant_ref)
        if tenant_ref == "admin":
            raise ServiceError("admin cannot be deleted")
        now = timestamp()
        with self.connect() as db:
            changed = db.execute("UPDATE tenants SET state='Deleting',updated_at=?,deleted_at=? WHERE tenant_ref=? AND state='Active'", (now, now, tenant_ref)).rowcount
            db.execute("UPDATE grants SET revoked_at=? WHERE tenant_ref=? AND revoked_at IS NULL", (now, tenant_ref))
            db.commit()
        if not changed:
            raise ServiceError("active tenant not found")
        return {"tenantRef": tenant_ref, "state": "Deleting", "grantsRevoked": True, "deletedAt": now}

    def create_grant(self, tenant_ref: str, consumable_ref: str, role_ref: str, visibility: str, constraints: dict[str, Any]) -> dict[str, Any]:
        tenant_ref = self.validate_tenant_ref(tenant_ref)
        if visibility not in VISIBILITIES:
            raise ServiceError("visibility must be Private, Tenant or Shared")
        if consumable_ref not in CATALOG or not role_ref.strip():
            raise ServiceError("published consumableRef and roleRef are required")
        if any(any(part in key.lower() for part in FORBIDDEN) for key in constraints):
            raise ServiceError("credential material is forbidden")
        with self.connect() as db:
            tenant = db.execute("SELECT state FROM tenants WHERE tenant_ref=?", (tenant_ref,)).fetchone()
            if not tenant or tenant["state"] != "Active":
                raise ServiceError("active tenant not found")
            grant_ref = f"grant://tenant/{tenant_ref}/{uuid.uuid4().hex}"
            access_ref = f"access://tenant/{tenant_ref}/{uuid.uuid4().hex}"
            try:
                db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,?,?,?)", (grant_ref, tenant_ref, consumable_ref, role_ref.strip(), visibility, json.dumps(constraints, sort_keys=True), access_ref, timestamp(), None))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ServiceError("active grant already exists") from exc
        return {"grantRef": grant_ref, "tenantRef": tenant_ref, "consumableRef": consumable_ref, "roleRef": role_ref.strip(), "visibility": visibility, "accessRef": access_ref, "credentialMaterialReturned": False}

    def register_cluster(self, tenant_ref: str, cluster_uid: str, region_ref: str, issuer: str, public_key: str, capabilities: list[str]) -> dict[str, Any]:
        tenant_ref = self.validate_tenant_ref(tenant_ref)
        if not all((cluster_uid.strip(), region_ref.strip(), issuer.startswith("https://"), public_key.strip())):
            raise ServiceError("clusterUid, regionRef, HTTPS issuer and publicKey are required")
        now = timestamp()
        registration_ref = f"cluster-registration://{tenant_ref}/{uuid.uuid4().hex}"
        enrollment_ref = f"enrollment://tenant/{tenant_ref}/{uuid.uuid4().hex}"
        with self.connect() as db:
            try:
                db.execute("INSERT INTO cluster_registrations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (registration_ref, tenant_ref, cluster_uid, region_ref, issuer, canonical_hash(public_key), json.dumps(sorted(set(capabilities))), "PendingApproval", enrollment_ref, now, now))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ServiceError("cluster is already registered for tenant") from exc
        return {"registrationRef": registration_ref, "tenantRef": tenant_ref, "clusterUid": cluster_uid, "regionRef": region_ref, "state": "PendingApproval", "enrollmentRef": enrollment_ref, "credentialMaterialReturned": False}

    def registrations(self, tenant_ref: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM cluster_registrations", []
        if tenant_ref:
            sql, args = sql + " WHERE tenant_ref=?", [tenant_ref]
        with self.connect() as db:
            rows = db.execute(sql + " ORDER BY created_at", args).fetchall()
        result = []
        for row in rows:
            value = dict(row); value["capabilities"] = json.loads(value.pop("capabilities_json")); value.pop("enrollment_ref", None)
            result.append(value)
        return result

    def approve_cluster(self, registration_ref: str) -> dict[str, Any]:
        now = timestamp()
        with self.connect() as db:
            registration = db.execute("SELECT * FROM cluster_registrations WHERE registration_ref=? AND state='PendingApproval'", (registration_ref,)).fetchone()
            if not registration:
                raise ServiceError("pending cluster registration not found")
            connection_ref = f"cluster-connection://{registration['tenant_ref']}/{uuid.uuid4().hex}"
            identity_ref = f"spiffe://re8ch.com/tenant/{registration['tenant_ref']}/cluster/{registration['cluster_uid']}"
            db.execute("INSERT INTO cluster_connections VALUES(?,?,?,?,?,?,?,?)", (connection_ref, registration_ref, registration["tenant_ref"], "Ready", identity_ref, now, None, now))
            db.execute("UPDATE cluster_registrations SET state='Approved',updated_at=? WHERE registration_ref=?", (now, registration_ref)); db.commit()
        return {"connectionRef": connection_ref, "registrationRef": registration_ref, "tenantRef": registration["tenant_ref"], "identityRef": identity_ref, "state": "Ready"}

    def connections(self, tenant_ref: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM cluster_connections", []
        if tenant_ref:
            sql, args = sql + " WHERE tenant_ref=?", [tenant_ref]
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created_at", args).fetchall()]

    def revoke_connection(self, tenant_ref: str, connection_ref: str, offline: bool = False) -> dict[str, Any]:
        now, state = timestamp(), "RevocationPending" if offline else "Revoked"
        with self.connect() as db:
            changed = db.execute("UPDATE cluster_connections SET state=?,revoked_at=? WHERE connection_ref=? AND tenant_ref=? AND state='Ready'", (state, now, connection_ref, tenant_ref)).rowcount; db.commit()
        if not changed:
            raise ServiceError("ready cluster connection not found")
        return {"connectionRef": connection_ref, "tenantRef": tenant_ref, "state": state, "revokedAt": now}

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        value = dict(row)
        value["spec"] = json.loads(value.pop("spec_json"))
        return value

    def plan(self, tenant: str, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        checked = validate_spec(spec)
        return {"decision": "preview", "tenantRef": tenant, "resourceRef": f"service://{tenant}/{name}", "requestHash": canonical_hash(checked), "intent": provisioning_intent(tenant, name, checked), "credentialMaterialReturned": False}

    def get(self, tenant: str, resource_ref: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM instances WHERE resource_ref=? AND tenant_ref=?", (resource_ref, tenant)).fetchone()
        if not row:
            raise ServiceError("service instance not found")
        return self._row(row) or {}

    def list(self, tenant: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM instances WHERE tenant_ref=? ORDER BY created_at", (tenant,)).fetchall()
        return [self._row(row) for row in rows]

    def mutate(self, tenant: str, name: str, spec: dict[str, Any], idempotency_key: str, action: str = "create") -> dict[str, Any]:
        if len(idempotency_key) < 8:
            raise ServiceError("idempotencyKey must contain at least 8 characters")
        resource_ref, created = f"service://{tenant}/{name}", timestamp()
        with self.lock, self.connect() as db:
            replay = db.execute("SELECT result_json,request_hash FROM operations WHERE tenant_ref=? AND idempotency_key=?", (tenant, idempotency_key)).fetchone()
            request_hash = canonical_hash({"action": action, "resourceRef": resource_ref, "spec": spec})
            if replay:
                if replay["request_hash"] != request_hash:
                    raise ServiceError("idempotencyKey was already used for a different request")
                return {**json.loads(replay["result_json"]), "idempotentReplay": True}
            existing = db.execute("SELECT * FROM instances WHERE resource_ref=? AND tenant_ref=?", (resource_ref, tenant)).fetchone()
            previous = self._row(existing)
            checked = validate_spec(spec, previous=previous["spec"] if previous else None)
            target = {"Ready": "Provisioning", "Suspended": "Suspended", "Deleted": "Deleting"}[checked["desiredState"]]
            generation = int(previous["generation"] + 1) if previous else 1
            if existing:
                db.execute("UPDATE instances SET service_class=?,state=?,spec_json=?,generation=?,updated_at=?,deleted_at=? WHERE resource_ref=? AND tenant_ref=?", (checked["serviceClass"], target, json.dumps(checked, sort_keys=True), generation, created, created if target == "Deleting" else None, resource_ref, tenant))
            else:
                db.execute("INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?,?)", (resource_ref, tenant, name, checked["serviceClass"], target, json.dumps(checked, sort_keys=True), generation, created, created, created if target == "Deleting" else None))
            operation_id = "op_" + uuid.uuid4().hex
            result = {"operationId": operation_id, "tenantRef": tenant, "resourceRef": resource_ref, "state": target, "generation": generation, "intent": provisioning_intent(tenant, name, checked), "credentialMaterialReturned": False}
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)", (operation_id, tenant, resource_ref, action, idempotency_key, request_hash, json.dumps(previous, sort_keys=True) if previous else None, json.dumps(checked, sort_keys=True), json.dumps(result, sort_keys=True), created))
            db.commit()
        return result

    def operation(self, tenant: str, operation_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=? AND tenant_ref=?", (operation_id, tenant)).fetchone()
        if not row:
            raise ServiceError("operation not found")
        value = dict(row)
        for key in ("before_json", "after_json", "result_json"):
            value[key.removesuffix("_json")] = json.loads(value.pop(key)) if value[key] else None
        return value

    def create_binding(self, tenant: str, resource_ref: str, binding_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.get(tenant, resource_ref)
        if any(any(x in key.lower() for x in FORBIDDEN) for key in parameters):
            raise ServiceError("credential material is forbidden")
        binding_ref, created = "binding://" + tenant + "/" + uuid.uuid4().hex, timestamp()
        value = {"bindingRef": binding_ref, "resourceRef": resource_ref, "bindingType": binding_type, "accessRef": f"access://tenant/{tenant}/{uuid.uuid4().hex}", "credentialMaterialReturned": False}
        with self.connect() as db:
            db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,?)", (binding_ref, tenant, resource_ref, binding_type, json.dumps({**value, "parameters": parameters}, sort_keys=True), None, created)); db.commit()
        return value

    def bindings(self, tenant: str, resource_ref: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM bindings WHERE tenant_ref=?", [tenant]
        if resource_ref:
            sql, args = sql + " AND resource_ref=?", [tenant, resource_ref]
        with self.connect() as db:
            rows = db.execute(sql + " ORDER BY created_at", args).fetchall()
        return [{**json.loads(row["binding_json"]), "revokedAt": row["revoked_at"]} for row in rows]

    def revoke_binding(self, tenant: str, binding_ref: str) -> dict[str, Any]:
        revoked = timestamp()
        with self.connect() as db:
            changed = db.execute("UPDATE bindings SET revoked_at=? WHERE binding_ref=? AND tenant_ref=? AND revoked_at IS NULL", (revoked, binding_ref, tenant)).rowcount; db.commit()
        if not changed:
            raise ServiceError("active binding not found")
        return {"bindingRef": binding_ref, "revokedAt": revoked, "credentialRevoked": True}

    def record_usage(self, tenant: str, resource_ref: str, meter: str, quantity: float, unit: str, period_start: str, period_end: str, source_operation_id: str) -> dict[str, Any]:
        instance = self.get(tenant, resource_ref)
        meters = CATALOG[instance["service_class"]]["meters"]
        if meters.get(meter) != unit or quantity < 0:
            raise ServiceError("meter or unit is not published for serviceClass")
        stable = canonical_hash([tenant, resource_ref, meter, period_start, period_end, source_operation_id])
        event = {"eventId": "ue_" + stable[7:39], "tenantRef": tenant, "resourceRef": resource_ref, "serviceClass": instance["service_class"], "meter": meter, "quantity": quantity, "unit": unit, "periodStart": period_start, "periodEnd": period_end, "regionRef": instance["spec"]["regionRef"], "sourceOperationId": source_operation_id, "observedAt": timestamp(), "schemaVersion": "usage.re8ch.com/v1"}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO usage_outbox VALUES(?,?,?,?,?,?)", (event["eventId"], tenant, resource_ref, json.dumps(event, sort_keys=True), None, timestamp())); db.commit()
        return event

    def usage_summary(self, tenant: str, resource_ref: str | None = None) -> dict[str, Any]:
        sql, args = "SELECT event_json FROM usage_outbox WHERE tenant_ref=?", [tenant]
        if resource_ref:
            sql, args = sql + " AND resource_ref=?", [tenant, resource_ref]
        with self.connect() as db:
            events = [json.loads(row[0]) for row in db.execute(sql, args).fetchall()]
        totals: dict[str, float] = {}
        for event in events:
            key = event["meter"] + ":" + event["unit"]
            totals[key] = totals.get(key, 0) + float(event["quantity"])
        return {"tenantRef": tenant, "resourceRef": resource_ref, "totals": totals, "eventCount": len(events), "pricingApplied": False, "accountingPosted": False}
