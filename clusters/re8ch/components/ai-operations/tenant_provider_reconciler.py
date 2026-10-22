#!/usr/bin/env python3
"""Reconcile tenant Harbor and Grafana access from granted service instances."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

import tenant_control_controller as control
from grafana_exploration import tenant_config
from harbor_integration import HarborClient, desired_tenant

LOG = logging.getLogger("tenant-provider-reconciler")
HARBOR_URL = os.getenv("HARBOR_URL", "https://registry.re8ch.com")
GRAFANA_URL = os.getenv("GRAFANA_URL", "https://grafana.service.re8ch.com/grafana")
GRAFANA_ADMIN_URL = os.getenv("GRAFANA_ADMIN_URL", "http://re8ch-grafana.observability-system.svc.cluster.local:3000")
METRICS_AUTH_URL = "http://vmauth-tenant-metrics.observability-system.svc.cluster.local:8427"
METRICS_VMSELECT_URL = "http://vmselect-re8ch-metrics.observability-system.svc.cluster.local:8481/select/0/prometheus/"
VMUSER_API = "apis/operator.victoriametrics.com/v1beta1"
METRICS_PATHS = [r"/api/v1/(query|query_range|labels|label/[^/]+/values|series|metadata|status/buildinfo)"]
IDENTITY_REST_URL = os.getenv("IDENTITY_REST_URL", "http://supabase-identity-rest.supabase-identity.svc:3000")


class IdentityStore:
    """Use Supabase identity's native PostgREST RPCs for user binding and config."""

    def __init__(self, base_url: str, service_key: str, opener=urllib.request.urlopen):
        if not service_key:
            raise RuntimeError("Supabase identity service credential is unavailable")
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.opener = opener

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}/rpc/{name}",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.service_key}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=20) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Supabase identity RPC returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError("Supabase identity RPC is unreachable") from exc
        return json.loads(body) if body else None

    def human_email(self, tenant: str, identity_user: str) -> str:
        rows = self.rpc("resolve_tenant_service_user", {"p_tenant_ref": tenant, "p_user_id": identity_user})
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("user_id") != identity_user:
            raise PermissionError("Supabase user is not an active member of the tenant")
        email = str(rows[0].get("email") or "")
        if not email or "@" not in email:
            raise PermissionError("Supabase user has no usable email")
        return email

    def record(self, instance: dict[str, Any], access: dict[str, Any] | None, phase: str) -> None:
        spec, meta = instance["spec"], instance["metadata"]
        access = access or {}
        provider_ref = str(access.get("project") or access.get("organizationId") or "")
        observed = {key: value for key, value in access.items() if key not in {"humanCredentialRef", "credentialMaterialReturned"}}
        self.rpc("upsert_tenant_service_config", {
            "p_tenant_ref": spec["tenantRef"], "p_service_class": spec["serviceClass"],
            "p_instance_uid": meta["uid"], "p_instance_name": meta["name"],
            "p_identity_user_id": spec["parameters"]["identityUserRef"],
            "p_provider_user_id": str(access.get("humanLogin") or ""),
            "p_provider_resource_ref": provider_ref, "p_desired_config": spec["parameters"],
            "p_observed_config": observed, "p_credential_ref": access.get("humanCredentialRef"),
            "p_lifecycle": spec["lifecycle"], "p_observed_phase": phase,
            "p_observed_generation": int(meta.get("generation", 1)),
        })


def identity_store() -> IdentityStore:
    return IdentityStore(IDENTITY_REST_URL, os.getenv("IDENTITY_SERVICE_KEY", ""))


def secret_value(kube, namespace: str, name: str, key: str) -> str:
    value = kube.get("secrets", name, namespace, "api/v1")
    encoded = (value.get("data") or {}).get(key)
    if not encoded:
        raise RuntimeError("provider credential is unavailable")
    return base64.b64decode(encoded).decode()


def managed_credential(kube, instance: dict[str, Any], provider: str, project: str, username: str, *, create_if_missing: bool = True) -> tuple[str | None, str | None]:
    """Create material before the external user, so retries recover after a crash."""
    tenant = instance["spec"]["tenantRef"]
    credential_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{provider}:{tenant}:{instance['metadata']['uid']}:human").hex
    name = f"{provider}-{credential_id}"
    reference = f"credential://{provider}/{tenant}/{credential_id}"
    try:
        existing = kube.get("secrets", name, "credential-broker", "api/v1")
        labels = existing.get("metadata", {}).get("labels", {})
        if labels.get("credentials.re8ch.com/tenant") != tenant or labels.get("credentials.re8ch.com/provider") != provider:
            raise PermissionError("managed credential ownership does not match")
        return reference, base64.b64decode(existing["data"]["password"]).decode()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    if not create_if_missing:
        return None, None
    password = secrets.token_urlsafe(32)
    value = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": "credential-broker", "labels": {"credentials.re8ch.com/provider": provider, "credentials.re8ch.com/tenant": tenant, "credentials.re8ch.com/project": project, "credentials.re8ch.com/type": "human"}}, "type": "Opaque", "stringData": {"username": username, "password": password, "project": project}}
    try:
        kube.create("secrets", value, "credential-broker", "api/v1")
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        return managed_credential(kube, instance, provider, project, username, create_if_missing=create_if_missing)
    return reference, password


class GrafanaAdmin:
    def __init__(self, base_url: str, username: str, password: str, opener=urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.opener = opener

    def request(self, method: str, path: str, payload: Any = None, org_id: int | None = None, *, missing_ok: bool = False) -> Any:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Authorization": self.authorization, "Accept": "application/json", "Content-Type": "application/json"}
        if org_id is not None:
            headers["X-Grafana-Org-Id"] = str(org_id)
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise RuntimeError(f"Grafana API returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError("Grafana API is unreachable") from exc
        return json.loads(raw) if raw else {}

    def user(self, login: str) -> dict[str, Any] | None:
        return self.request("GET", "/api/users/lookup?loginOrEmail=" + urllib.parse.quote(login, safe=""), missing_ok=True)

    def reset_user_password(self, user_id: int, password: str) -> None:
        self.request("PUT", f"/api/admin/users/{user_id}/password", {"password": password})

    def ensure_human(self, org_id: int, login: str, email: str, role: str, password: str | None) -> int:
        user = self.user(login)
        if user is None:
            if not password:
                raise RuntimeError("Grafana human account password is unavailable")
            self.request("POST", "/api/admin/users", {"name": login, "email": email, "login": login, "password": password})
            user = self.user(login)
        if not user or str(user.get("email", "")).lower() != email.lower():
            raise PermissionError("Grafana human account identity does not match")
        user_id = int(user["id"])
        members = self.request("GET", f"/api/orgs/{org_id}/users")
        member = next((item for item in members if int(item.get("userId") or item.get("userID") or 0) == user_id), None)
        if member is None:
            self.request("POST", f"/api/orgs/{org_id}/users", {"loginOrEmail": login, "role": role})
        elif member.get("role") != role:
            self.request("PATCH", f"/api/orgs/{org_id}/users/{user_id}", {"role": role})
        if org_id != 1:
            main_members = self.request("GET", "/api/orgs/1/users")
            if any(int(item.get("userId") or item.get("userID") or 0) == user_id for item in main_members):
                self.request("DELETE", f"/api/orgs/1/users/{user_id}")
        return user_id


def harbor_client(kube) -> HarborClient:
    return HarborClient(HARBOR_URL, secret_value(kube, "ai-operations", "cluster-infra-harbor", "username"), secret_value(kube, "ai-operations", "cluster-infra-harbor", "password"))


def grafana_client(kube) -> GrafanaAdmin:
    return GrafanaAdmin(GRAFANA_ADMIN_URL, secret_value(kube, "observability-system", "re8ch-grafana-admin", "user"), secret_value(kube, "observability-system", "re8ch-grafana-admin", "password"))


def auto_grafana_name(tenant: str, namespace: str) -> str:
    return f"Tenant {tenant} {namespace}"


def auto_metrics_id(tenant: str, namespace: str, kind: str) -> str:
    digest = hashlib.sha256(f"{tenant}:{namespace}".encode()).hexdigest()[:20]
    uid = f"tenant-{digest}-{kind}"
    if len(uid) > 40:
        raise ValueError("Grafana datasource UID exceeds the 40-character limit")
    return uid


def auto_org(client: GrafanaAdmin, tenant: str, namespace: str, *, create: bool) -> dict[str, Any] | None:
    name = auto_grafana_name(tenant, namespace)
    path = "/api/orgs/name/" + urllib.parse.quote(name, safe="")
    org = client.request("GET", path, missing_ok=True)
    if org is None and create:
        client.request("POST", "/api/orgs", {"name": name})
        org = client.request("GET", path)
    if org and (org.get("name") != name or int(org.get("id") or 0) <= 1):
        raise PermissionError("Grafana organization identity is unsafe")
    return org


def database_bindings(kube, tenant: str) -> list[str]:
    """Accept only operator-pinned credentials in a namespace owned by this tenant."""
    entries = json.loads(os.getenv("GRAFANA_DATABASE_BINDINGS_JSON", "[]"))
    if not isinstance(entries, list):
        raise PermissionError("database metrics bindings must be a list")
    seen = set()
    databases = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"tenantRef", "database", "namespace", "secretName", "secretUID"}:
            raise PermissionError("database metrics binding has invalid fields")
        db, owner, ns, name, uid = (entry[key] for key in ("database", "tenantRef", "namespace", "secretName", "secretUID"))
        if (not all(isinstance(value, str) for value in (db, owner, ns, name, uid))
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", db)
                or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", ns)
                or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
                or not re.fullmatch(r"[0-9a-f-]{36}", uid) or db in seen):
            raise PermissionError("database metrics binding is ambiguous or invalid")
        seen.add(db)
        if owner != tenant:
            continue
        namespace = kube.get("namespaces", ns, api="api/v1")
        if (namespace.get("metadata", {}).get("labels") or {}).get("tenancy.re8ch.com/tenant") != tenant:
            raise PermissionError("database credential namespace is outside the tenant")
        credential = kube.get("secrets", name, ns, "api/v1")
        meta = credential.get("metadata") or {}
        if (meta.get("uid") != uid or (meta.get("labels") or {}).get("re8ch.com/database") != db
                or (meta.get("labels") or {}).get("re8ch.com/secret-purpose") != "postgres-credentials"
                or base64.b64decode((credential.get("data") or {}).get("PGDATABASE", "")).decode() != db):
            raise PermissionError("database credential ownership or identity has changed")
        databases.append(db)
    return sorted(databases)


def auto_vmuser(kube, tenant: str, namespace: str, kind: str, database: str | None = None) -> tuple[str, str]:
    name = auto_metrics_id(tenant, namespace, kind)
    if database:
        filters = [("datname", database), ("job", "pigsty-pg-re8ch-exporters")]
    elif kind == "app":
        filters = [("namespace", namespace)]
    elif kind == "kube":
        filters = [("exported_namespace", namespace), ("job", "re8ch-kube-state-metrics")]
    else:
        raise PermissionError("unsupported metrics scope")
    url = METRICS_VMSELECT_URL + "?" + urllib.parse.urlencode([("extra_label", f"{key}={value}") for key, value in filters])
    labels = {"observability.re8ch.com/tenant-metrics-user": "true", "tenancy.re8ch.com/tenant": tenant,
              "observability.re8ch.com/runtime-namespace": namespace, "observability.re8ch.com/metric-kind": "db" if database else kind}
    if database:
        labels["observability.re8ch.com/database"] = database
    spec = {"username": name, "generatePassword": True, "targetRefs": [{"static": {"url": url}, "paths": METRICS_PATHS}]}
    value = {"apiVersion": "operator.victoriametrics.com/v1beta1", "kind": "VMUser",
             "metadata": {"name": name, "namespace": "credential-broker", "labels": labels}, "spec": spec}
    try:
        existing = kube.get("vmusers", name, "credential-broker", VMUSER_API)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        try:
            existing = kube.create("vmusers", value, "credential-broker", VMUSER_API)
        except urllib.error.HTTPError as conflict:
            if conflict.code != 409:
                raise
            existing = kube.get("vmusers", name, "credential-broker", VMUSER_API)
    if (existing.get("metadata", {}).get("labels") or {}) != labels or existing.get("spec") != spec:
        raise PermissionError("VMUser tenant scope or ownership differs from the declaration")
    try:
        credential = kube.get("secrets", "vmuser-" + name, "credential-broker", "api/v1")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("VMUser credential is still being generated") from None
        raise
    owners = credential.get("metadata", {}).get("ownerReferences") or []
    if not any(owner.get("uid") == existing.get("metadata", {}).get("uid") and owner.get("kind") == "VMUser" for owner in owners):
        raise PermissionError("VMUser credential owner does not match")
    data = credential.get("data") or {}
    username = base64.b64decode(data.get("username", "")).decode()
    password = base64.b64decode(data.get("password", "")).decode()
    if username != name or not password:
        raise RuntimeError("VMUser generated credential is invalid")
    return username, password


def ensure_auto_grafana_scope(kube, instance: dict[str, Any], client: GrafanaAdmin) -> tuple[int, dict[str, dict[str, str]]]:
    tenant, namespace = instance["spec"]["tenantRef"], instance["metadata"]["namespace"]
    if control.runtime_namespace(tenant) != namespace:
        raise PermissionError("Grafana metrics namespace is outside the active tenant")
    parameters = instance["spec"]["parameters"]
    if set(parameters["datasourceRefs"]) != {"app", "kube"} or parameters["folderRefs"]:
        raise PermissionError("Grafana auto provisioning requires app and kube metrics without external folders")
    try:
        databases = database_bindings(kube, tenant)
    except Exception:
        revoke_grafana(instance, client)
        raise
    org = auto_org(client, tenant, namespace, create=True)
    org_id = int(org["id"])
    prior_org = ((instance.get("status") or {}).get("access") or {}).get("organizationId")
    if prior_org and int(prior_org) != org_id:
        revoke_grafana(instance, client)
        raise PermissionError("Grafana tenant organization identity has changed")
    sources = [(kind, None) for kind in ("app", "kube")]
    sources += [("db-" + hashlib.sha256(db.encode()).hexdigest()[:8], db) for db in databases]
    expected_uids = {auto_metrics_id(tenant, namespace, kind) for kind, _ in sources}
    existing = client.request("GET", "/api/datasources", org_id=org_id)
    extras = {item.get("uid") for item in existing} - expected_uids
    if extras:
        revoke_grafana(instance, client)
        managed_prefix = auto_metrics_id(tenant, namespace, "db-")
        if any(not isinstance(uid, str) or not uid.startswith(managed_prefix) for uid in extras):
            raise PermissionError("Grafana tenant organization has an undeclared datasource")
        for uid in extras:
            client.request("DELETE", "/api/datasources/uid/" + uid, org_id=org_id)
    bindings = {}
    for kind, database in sources:
        uid = auto_metrics_id(tenant, namespace, kind)
        try:
            username, password = auto_vmuser(kube, tenant, namespace, kind, database)
        except PermissionError:
            revoke_grafana(instance, client)
            raise
        credential_hash = hashlib.sha256(password.encode()).hexdigest()
        desired = {"uid": uid, "name": f"Tenant {tenant} {namespace} {database or kind}", "type": "prometheus", "access": "proxy",
                   "url": METRICS_AUTH_URL, "basicAuth": True, "basicAuthUser": username,
                   "secureJsonData": {"basicAuthPassword": password},
                   "jsonData": {"httpMethod": "POST", "re8chCredentialHash": credential_hash}}
        datasource = client.request("GET", "/api/datasources/uid/" + uid, org_id=org_id, missing_ok=True)
        if datasource is None:
            client.request("POST", "/api/datasources", desired, org_id=org_id)
        elif (datasource.get("orgId") != org_id or datasource.get("uid") != uid
              or datasource.get("type") != "prometheus" or datasource.get("access") != "proxy"
              or datasource.get("url") != METRICS_AUTH_URL or not datasource.get("basicAuth")
              or datasource.get("basicAuthUser") != username):
            revoke_grafana(instance, client)
            raise PermissionError("Grafana datasource tenant boundary has drifted")
        elif (datasource.get("jsonData") or {}).get("re8chCredentialHash") != credential_hash:
            client.request("PUT", f"/api/datasources/uid/{uid}", desired, org_id=org_id)
        bindings[uid] = {"url": METRICS_AUTH_URL, "basicAuthUser": username}
    # A generated VMUser Secret may precede VMAuth's config reload. Check the
    # same Grafana proxy path a browser or HTTP API user will use before Ready.
    for uid in bindings:
        response = client.request("GET", f"/api/datasources/proxy/uid/{uid}/api/v1/query?query=up", org_id=org_id)
        if response.get("status") != "success":
            raise RuntimeError("Grafana tenant datasource is not queryable yet")
    return org_id, bindings


def previously_managed_account(kube, instance: dict[str, Any], login: str) -> bool:
    """Only a terminal instance for this same user authorizes password recovery."""
    spec, meta = instance["spec"], instance["metadata"]
    for previous in kube.list("tenantserviceinstances", meta["namespace"]):
        old_spec, old_meta = previous.get("spec") or {}, previous.get("metadata") or {}
        old_status = previous.get("status") or {}
        access = old_status.get("access") or {}
        if old_meta.get("uid") == meta["uid"]:
            continue
        if (old_spec.get("tenantRef") == spec["tenantRef"]
                and old_spec.get("serviceClass") == spec["serviceClass"]
                and (old_spec.get("parameters") or {}).get("identityUserRef") == spec["parameters"]["identityUserRef"]
                and old_spec.get("lifecycle") == "Deleting" and old_status.get("phase") == "Deleted"
                and access.get("humanLogin") == login and access.get("humanCredentialRef")):
            return True
    return False


def reconcile_harbor(kube, instance: dict[str, Any], client: HarborClient, identity_email: str) -> dict[str, Any]:
    tenant = instance["spec"]["tenantRef"]
    project = instance["spec"]["parameters"]["project"]
    catalog = json.loads(kube.get("configmaps", "harbor-tenant-catalog", "ai-operations", "api/v1")["data"]["harbor-tenants.json"])["tenants"]
    # The Tenant declaration and class grant are the authority for new tenants.
    # Legacy catalog entries only preserve existing native account names.
    desired = desired_tenant(tenant, catalog.get(tenant))
    if desired["project"] != project:
        raise PermissionError("Harbor catalog project differs from TenantDefinition")
    declared_email = ((catalog.get(tenant) or {}).get("harbor") or {}).get("accountEmail")
    if declared_email and declared_email.lower() != identity_email.lower():
        raise PermissionError("Harbor catalog email differs from the bound Supabase user")
    quota = instance["spec"]["parameters"]["quotaGiB"]
    live = client.find_project(project)
    if not live:
        client.create_project(project, quota)
        live = client.find_project(project)
    if not live or (live.get("metadata") or {}).get("public") not in {"false", False}:
        raise RuntimeError("private Harbor project could not be verified")
    client.ensure_project_quota(int(live["project_id"]), quota)
    user = client.find_user(desired["username"])
    reference = instance.get("status", {}).get("humanCredentialRef")
    if not user:
        reference, password = managed_credential(kube, instance, "harbor", project, desired["username"])
        client.create_user(desired["username"], identity_email, password or "")
    elif str(user.get("email") or "").lower() != identity_email.lower():
        raise PermissionError("existing Harbor account email differs from the bound Supabase user")
    elif not reference:
        reference, password = managed_credential(kube, instance, "harbor", project, desired["username"], create_if_missing=False)
        if not reference and previously_managed_account(kube, instance, desired["username"]):
            reference, password = managed_credential(kube, instance, "harbor", project, desired["username"])
            client.reset_user_password(int(user["user_id"]), password or "")
    if not reference:
        raise RuntimeError("existing Harbor human account requires credential adoption")
    role_id = {"pull": 3, "build-push": 2, "project-admin": 1}[instance["spec"]["plan"]]
    client.ensure_project_role(project, desired["username"], role_id)
    for robot_id in instance.get("status", {}).get("suspendedRobotIds") or []:
        client.disable_robot(int(robot_id), False)
    if not client.find_user(desired["username"]) or not client.find_project(project):
        raise RuntimeError("Harbor project or human account verification failed")
    return {"project": project, "humanLogin": desired["username"], "humanCredentialRef": reference, "webUrl": HARBOR_URL, "quotaGiB": quota, "nativeRoleId": role_id, "credentialMaterialReturned": False}


def reconcile_harbor_robot(kube, instance: dict[str, Any], client: HarborClient) -> dict[str, Any]:
    """Give a tenant build or pull access without binding a human account."""
    spec, meta = instance["spec"], instance["metadata"]
    tenant, project, plan = spec["tenantRef"], spec["parameters"]["project"], spec["plan"]
    if plan not in {"pull", "build-push"} or project != control.harbor_project(tenant):
        raise PermissionError("tenant robot plan or project is invalid")
    quota = spec["parameters"]["quotaGiB"]
    live = client.find_project(project)
    if not live:
        client.create_project(project, quota)
        live = client.find_project(project)
    if not live or (live.get("metadata") or {}).get("public") not in {"false", False}:
        raise RuntimeError("private Harbor project could not be verified")
    client.ensure_project_quota(int(live["project_id"]), quota)

    credential_id = uuid.uuid5(uuid.NAMESPACE_URL, f"harbor:{tenant}:{meta['uid']}:tenant-robot").hex
    secret_name = f"harbor-{credential_id}"
    reference = f"credential://harbor/{tenant}/{credential_id}"
    description = f"RE8CH tenant-owned robot for {meta['uid']}"
    existing = None
    try:
        existing = kube.get("secrets", secret_name, "credential-broker", "api/v1")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    old_id = None
    old_material = None
    if existing:
        labels = existing.get("metadata", {}).get("labels", {})
        if any(labels.get(f"credentials.re8ch.com/{key}") != value for key, value in
               {"provider": "harbor", "tenant": tenant, "project": project, "type": "robot", "instance-uid": meta["uid"]}.items()):
            raise PermissionError("tenant robot credential ownership does not match")
        data = existing.get("data") or {}
        old_id = int(base64.b64decode(data["robot_id"]).decode())
        if not data.get("username") or not data.get("password"):
            raise RuntimeError("tenant robot credential is incomplete")
        old_material = {key: base64.b64decode(data[key]).decode()
                        for key in ("username", "password", "project", "robot_id")}
        try:
            robot = client.robot(old_id)
        except Exception:
            robot = None
        expected_access = ({("repository", "pull")} if plan == "pull" else
                           {("repository", "pull"), ("repository", "push"),
                            ("artifact", "read"), ("artifact", "create"), ("scan", "create")})
        permissions = (robot.get("permissions") or []) if robot else []
        actual_access = {(entry.get("resource"), entry.get("action"))
                         for permission in permissions for entry in permission.get("access") or []}
        reusable = (robot and not robot.get("disable") and robot.get("description") == description
                and robot.get("level") == "project"
                and permissions and all(permission.get("namespace") == project for permission in permissions)
                and actual_access == expected_access
                and labels.get("credentials.re8ch.com/plan") == plan
                and int(robot.get("expires_at") or 0) > int(time.time()) + 86400)
        if reusable:
            try:
                client.probe_robot_credential(old_material["username"], old_material["password"], project)
            except Exception:
                reusable = False
        if reusable:
            return {"project": project, "robotCredentialRef": reference, "robotId": old_id,
                    "quotaGiB": quota, "credentialVerified": True,
                    "credentialMaterialReturned": False}

    robot, token = client.create_robot(project, f"tenant-{credential_id[:16]}-{secrets.token_hex(3)}",
                                       plan, spec["parameters"]["robotDurationDays"], description=description)
    robot_id = int(robot["id"])
    material = {"username": str(robot["name"]), "password": token, "project": project, "robot_id": str(robot_id)}
    labels = {"credentials.re8ch.com/provider": "harbor", "credentials.re8ch.com/tenant": tenant,
              "credentials.re8ch.com/project": project, "credentials.re8ch.com/type": "robot",
              "credentials.re8ch.com/instance-uid": meta["uid"], "credentials.re8ch.com/plan": plan}
    try:
        client.probe_robot_credential(material["username"], material["password"], project)
        if existing:
            kube.patch("secrets", secret_name, {"metadata": {"labels": labels}, "stringData": material},
                       "credential-broker", "api/v1")
        else:
            kube.create("secrets", {"apiVersion": "v1", "kind": "Secret",
                                    "metadata": {"name": secret_name, "namespace": "credential-broker", "labels": labels},
                                    "type": "Opaque", "stringData": material}, "credential-broker", "api/v1")
    except Exception:
        client.disable_robot(robot_id, True)
        raise
    if old_id and old_id != robot_id:
        client.disable_robot(old_id, True)
    return {"project": project, "robotCredentialRef": reference, "robotId": robot_id,
            "quotaGiB": quota, "credentialVerified": True,
            "credentialMaterialReturned": False}


def revoke_harbor(kube, instance: dict[str, Any], client: HarborClient) -> list[int]:
    tenant = instance["spec"]["tenantRef"]
    project = instance["spec"]["parameters"]["project"]
    if project != control.harbor_project(tenant):
        raise PermissionError("Harbor project is outside the tenant")
    suspended = set(instance.get("status", {}).get("suspendedRobotIds") or [])
    own_robot = not instance["spec"]["parameters"].get("identityUserRef")
    own_description = f"RE8CH tenant-owned robot for {instance['metadata']['uid']}"
    robots = client.robots()
    def owned(robot):
        permissions = robot.get("permissions") or []
        if robot.get("level") != "project" or not permissions or any(item.get("namespace") != project for item in permissions):
            return False
        description = str(robot.get("description") or "")
        return description == own_description if own_robot else not description.startswith("RE8CH tenant-owned robot for ")
    suspended &= {int(robot["id"]) for robot in robots if owned(robot)}
    for robot in robots:
        if not owned(robot):
            continue
        robot_id = int(robot["id"])
        if not robot.get("disable"):
            suspended.add(robot_id)
    # Persist the exact restore set before changing the provider. A crash after
    # revocation must not lose which robots were active before suspension.
    kube.patch_status("tenantserviceinstances", instance["metadata"]["name"], {"phase": "Revoking", "suspendedRobotIds": sorted(suspended)}, instance["metadata"]["namespace"])
    for robot in robots:
        if int(robot.get("id") or 0) in suspended and not robot.get("disable"):
            client.disable_robot(int(robot["id"]), True)
    login = None
    if not own_robot:
        catalog = json.loads(kube.get("configmaps", "harbor-tenant-catalog", "ai-operations", "api/v1")["data"]["harbor-tenants.json"])["tenants"]
        login = desired_tenant(tenant, catalog.get(tenant))["username"]
    if login:
        for member in client.project_members(project):
            if (member.get("entity_name") or (member.get("entity") or {}).get("entity_name")) == login:
                client.remove_project_member(project, int(member["id"]))
        if any((member.get("entity_name") or (member.get("entity") or {}).get("entity_name")) == login for member in client.project_members(project)):
            raise RuntimeError("Harbor human project membership could not be revoked")
    if any(owned(robot) and not robot.get("disable") for robot in client.robots()):
        raise RuntimeError("Harbor project robot access could not be revoked")
    return sorted(suspended)


def reconcile_grafana(kube, instance: dict[str, Any], client: GrafanaAdmin, identity_email: str) -> dict[str, Any]:
    tenant = instance["spec"]["tenantRef"]
    auto = not os.getenv("GRAFANA_TENANT_BINDINGS_JSON")
    if auto:
        org_id, bindings = ensure_auto_grafana_scope(kube, instance, client)
    else:
        config = tenant_config(tenant)
        org_id = int(config["orgId"])
        bindings = config.get("datasourceBindings") or {}
    if org_id == 1:
        raise PermissionError("Main Org cannot be assigned to a tenant")
    organization = client.request("GET", f"/api/orgs/{org_id}")
    if not organization or int(organization.get("id", 0)) != org_id:
        raise RuntimeError("Grafana tenant organization is unavailable")
    parameters = instance["spec"]["parameters"]
    datasources = client.request("GET", "/api/datasources", org_id=org_id)
    visible = {item.get("uid") for item in datasources}
    requested = set(bindings) if auto else set(parameters["datasourceRefs"])
    # Grafana viewers can query every datasource in their Org, regardless of
    # datasourceRefs on the service instance. Attest the entire Org before
    # granting membership, including the server-side vmauth identity.
    if not requested or visible != set(bindings) or not requested <= visible:
        revoke_grafana(instance, client)
        raise PermissionError("Grafana tenant datasource scope is unavailable or unsafe")
    for uid, binding in bindings.items():
        datasource = client.request("GET", "/api/datasources/uid/" + urllib.parse.quote(uid, safe=""), org_id=org_id)
        if (datasource.get("orgId") != org_id or datasource.get("type") != "prometheus"
                or datasource.get("access") != "proxy" or datasource.get("url") != binding.get("url")
                or not datasource.get("basicAuth") or datasource.get("basicAuthUser") != binding.get("basicAuthUser")
                or not (datasource.get("secureJsonFields") or {}).get("basicAuthPassword")):
            revoke_grafana(instance, client)
            raise PermissionError("Grafana tenant datasource authentication or boundary is unsafe")
    folders = client.request("GET", "/api/folders", org_id=org_id)
    requested_folders = set(parameters["folderRefs"])
    if not requested_folders <= {item.get("uid") for item in folders}:
        raise PermissionError("Grafana tenant folder scope is unavailable")
    login, email = parameters["humanLogin"], identity_email
    user = client.user(login)
    reference = instance.get("status", {}).get("humanCredentialRef")
    password = None
    if not user:
        reference, password = managed_credential(kube, instance, "grafana", str(org_id), login)
    elif not reference:
        if str(user.get("email") or "").lower() != email.lower():
            raise PermissionError("existing Grafana human account identity does not match")
        reference, password = managed_credential(kube, instance, "grafana", str(org_id), login, create_if_missing=False)
        if not reference and previously_managed_account(kube, instance, login):
            reference, password = managed_credential(kube, instance, "grafana", str(org_id), login)
            client.reset_user_password(int(user["id"]), password or "")
    if not reference:
        raise RuntimeError("existing Grafana human account requires credential adoption")
    client.ensure_human(org_id, login, email, "Viewer" if instance["spec"]["plan"] == "viewer" else "Editor", password)
    return {"organizationId": org_id, "humanLogin": login, "humanCredentialRef": reference, "webUrl": GRAFANA_URL, "datasourceRefs": sorted(requested), "credentialMaterialReturned": False}


def revoke_grafana(instance: dict[str, Any], client: GrafanaAdmin) -> None:
    tenant = instance["spec"]["tenantRef"]
    prior_org = ((instance.get("status") or {}).get("access") or {}).get("organizationId")
    if prior_org:
        org_id = int(prior_org)
    elif os.getenv("GRAFANA_TENANT_BINDINGS_JSON"):
        org_id = int(tenant_config(tenant)["orgId"])
    else:
        org = auto_org(client, tenant, instance["metadata"]["namespace"], create=False)
        if not org:
            return
        org_id = int(org["id"])
    if org_id == 1:
        raise PermissionError("Main Org cannot be assigned to a tenant")
    login = instance["spec"]["parameters"]["humanLogin"]
    user = client.user(login)
    if not user:
        return
    user_id = int(user["id"])
    members = client.request("GET", f"/api/orgs/{org_id}/users")
    if any(int(item.get("userId") or item.get("userID") or 0) == user_id for item in members):
        client.request("DELETE", f"/api/orgs/{org_id}/users/{user_id}")
    remaining = client.request("GET", f"/api/orgs/{org_id}/users")
    if any(int(item.get("userId") or item.get("userID") or 0) == user_id for item in remaining):
        raise RuntimeError("Grafana tenant organization membership could not be revoked")


def reconcile_instance(kube, instance: dict[str, Any], harbor_factory=harbor_client, grafana_factory=grafana_client, identity_factory=identity_store) -> None:
    spec, meta = instance["spec"], instance["metadata"]
    service_class = spec.get("serviceClass")
    if service_class not in control.PROVIDER_SERVICE_CLASSES:
        return
    namespace, tenant = meta["namespace"], spec["tenantRef"]
    if meta.get("labels", {}).get("tenancy.re8ch.com/tenant") != tenant:
        raise PermissionError("service instance tenant label does not match")
    control.KUBE = kube
    lifecycle = spec.get("lifecycle")
    if (lifecycle == "Deleting" and (instance.get("status") or {}).get("phase") == "Deleted"
            and (instance.get("status") or {}).get("observedGeneration") == meta.get("generation", 1)):
        return
    identity = identity_factory() if spec.get("parameters", {}).get("identityUserRef") else None
    if lifecycle in {"Suspended", "Deleting"}:
        prior = instance.get("status") or {}
        suspended_robots = revoke_harbor(kube, instance, harbor_factory(kube)) if service_class == "registry.harbor.v1" else None
        if service_class == "observability.grafana.v1":
            revoke_grafana(instance, grafana_factory(kube))
        phase = "Suspended" if lifecycle == "Suspended" else "Deleted"
        if identity:
            identity.record(instance, prior.get("access"), phase)
        status = {"observedGeneration": meta.get("generation", 1), "phase": phase, "reason": None, "access": prior.get("access"), "humanCredentialRef": prior.get("humanCredentialRef"), "conditions": [control.condition("Ready", False, "ProviderAccessRevoked")]}
        if suspended_robots is not None:
            status["suspendedRobotIds"] = suspended_robots
        kube.patch_status("tenantserviceinstances", meta["name"], status, namespace)
        return
    if lifecycle != "Requested":
        raise PermissionError("unsupported provider lifecycle")
    request = {"serviceClass": service_class, "planRef": spec["plan"], "parameters": spec.get("parameters") or {}}
    normalized = control.provider_service_spec(tenant, meta["name"], request)
    if normalized != spec.get("parameters"):
        raise PermissionError("provider service parameters differ from the controller contract")
    if service_class == "registry.harbor.v1" and identity is None:
        access = reconcile_harbor_robot(kube, instance, harbor_factory(kube))
        kube.patch_status("tenantserviceinstances", meta["name"], {
            "observedGeneration": meta.get("generation", 1), "phase": "Ready", "reason": None,
            "access": access, "conditions": [control.condition("Ready", True, "ProviderAccessReady")],
            "suspendedRobotIds": [], "humanCredentialRef": None,
        }, namespace)
        return
    try:
        identity_email = identity.human_email(tenant, spec["parameters"]["identityUserRef"])
    except PermissionError:
        prior = instance.get("status") or {}
        if not prior.get("access"):
            raise
        suspended_robots = revoke_harbor(kube, instance, harbor_factory(kube)) if service_class == "registry.harbor.v1" else None
        if service_class == "observability.grafana.v1":
            revoke_grafana(instance, grafana_factory(kube))
        status = {
            "observedGeneration": meta.get("generation", 1),
            "phase": "Suspended", "reason": "IdentityMembershipRevoked",
            "access": prior["access"], "humanCredentialRef": prior.get("humanCredentialRef"),
            "conditions": [control.condition("Ready", False, "IdentityMembershipRevoked")],
        }
        if suspended_robots is not None:
            status["suspendedRobotIds"] = suspended_robots
        kube.patch_status("tenantserviceinstances", meta["name"], status, namespace)
        return
    access = reconcile_harbor(kube, instance, harbor_factory(kube), identity_email) if service_class == "registry.harbor.v1" else reconcile_grafana(kube, instance, grafana_factory(kube), identity_email)
    identity.record(instance, access, "Ready")
    status = {"observedGeneration": meta.get("generation", 1), "phase": "Ready", "reason": None, "access": access, "humanCredentialRef": access.get("humanCredentialRef"), "conditions": [control.condition("Ready", True, "ProviderAccessReady")]}
    if service_class == "registry.harbor.v1":
        status["suspendedRobotIds"] = []
    kube.patch_status("tenantserviceinstances", meta["name"], status, namespace)


def run_once(kube) -> None:
    for instance in kube.list("tenantserviceinstances"):
        if instance.get("spec", {}).get("serviceClass") not in control.PROVIDER_SERVICE_CLASSES:
            continue
        try:
            reconcile_instance(kube, instance)
        except Exception as exc:
            LOG.exception("provider reconcile failed for %s/%s", instance.get("metadata", {}).get("namespace"), instance.get("metadata", {}).get("name"))
            meta = instance["metadata"]
            kube.patch_status("tenantserviceinstances", meta["name"], {"observedGeneration": meta.get("generation", 1), "phase": "Failed", "reason": str(exc)[:256], "conditions": [control.condition("Ready", False, "ProviderReconcileFailed")]}, meta["namespace"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = control.Kubernetes()
    while True:
        try:
            run_once(client)
        except Exception:
            LOG.exception("provider scan failed")
        time.sleep(15)
