"""Harbor v2 and opaque credential handling for the RE8CH tool plane."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


SENSITIVE_KEYS = {"password", "secret", "token", "access_token", "refresh_token", "authorization"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,254}$")


class HarborError(RuntimeError):
    def __init__(self, status: int, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def sanitized(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


class HarborClient:
    def __init__(self, base_url: str, username: str, password: str,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        if not username or not password:
            raise HarborError(503, "harbor-api credential is unavailable")
        self.base_url = base_url.rstrip("/") + "/api/v2.0"
        self.username = username
        self.password = password
        self.opener = opener

    def request(self, method: str, path: str, payload: Any | None = None,
                *, expected: tuple[int, ...] = (200, 201, 202, 204), sanitize_response: bool = True) -> Any:
        raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        request = urllib.request.Request(
            self.base_url + path,
            data=raw,
            method=method,
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=30) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                detail = json.loads(body).get("errors", [{}])[0].get("message")
            except (ValueError, AttributeError, IndexError):
                detail = None
            raise HarborError(exc.code, detail or f"Harbor API returned HTTP {exc.code}", retryable=exc.code >= 500) from None
        except urllib.error.URLError as exc:
            raise HarborError(503, "Harbor API is unreachable", retryable=True) from exc
        if status not in expected:
            raise HarborError(status, f"unexpected Harbor response HTTP {status}")
        if not body:
            return {}
        try:
            decoded = json.loads(body)
            return sanitized(decoded) if sanitize_response else decoded
        except ValueError:
            return {"status": status}

    def paged(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{separator}page={page}&page_size=100")
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    @staticmethod
    def quote(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def users(self) -> list[dict[str, Any]]:
        return self.paged("/users")

    def projects(self) -> list[dict[str, Any]]:
        return self.paged("/projects")

    def repositories(self, project: str) -> list[dict[str, Any]]:
        return self.paged(f"/projects/{self.quote(project)}/repositories")

    def project(self, project: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{self.quote(project)}")

    def artifacts(self, project: str, repository: str) -> list[dict[str, Any]]:
        return self.paged(f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts?with_tag=true&with_scan_overview=true")

    def artifact(self, project: str, repository: str, reference: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}?with_tag=true&with_scan_overview=true&with_accessory=true")

    def accessories(self, project: str, repository: str, reference: str) -> list[dict[str, Any]]:
        return self.paged(f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/accessories")

    def vulnerability_report(self, project: str, repository: str, reference: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/additions/vulnerabilities")

    def sbom(self, project: str, repository: str, reference: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/additions/sbom")

    def audit_logs(self, project: str) -> list[dict[str, Any]]:
        query = urllib.parse.quote(f"project_name={project}", safe="=")
        return self.paged(f"/audit-logs?q={query}")

    def robots(self) -> list[dict[str, Any]]:
        return self.paged("/robots")

    def find_user(self, username: str) -> dict[str, Any] | None:
        return next((item for item in self.users() if item.get("username") == username), None)

    def find_project(self, project: str) -> dict[str, Any] | None:
        return next((item for item in self.projects() if item.get("name") == project), None)

    def create_user(self, username: str, email: str, password: str) -> dict[str, Any]:
        return self.request("POST", "/users", {"username": username, "email": email, "realname": username, "password": password, "admin_role_in_auth": False})

    def update_user(self, user_id: int, email: str, realname: str) -> None:
        self.request("PUT", f"/users/{user_id}", {"email": email, "realname": realname})

    def reset_user_password(self, user_id: int, password: str) -> None:
        self.request("PUT", f"/users/{user_id}/password", {"new_password": password})

    def create_project(self, project: str) -> dict[str, Any]:
        return self.request("POST", "/projects", {"project_name": project, "public": False, "metadata": {"public": "false", "auto_scan": "true"}})

    def project_members(self, project: str) -> list[dict[str, Any]]:
        return self.paged(f"/projects/{self.quote(project)}/members")

    def ensure_project_admin(self, project: str, username: str) -> bool:
        for member in self.project_members(project):
            entity = member.get("entity") or {}
            if entity.get("entity_name") == username and member.get("role_id") == 1:
                return False
        self.request("POST", f"/projects/{self.quote(project)}/members", {"role_id": 1, "member_user": {"username": username}})
        return True

    def remove_project_member(self, project: str, member_id: int) -> None:
        self.request("DELETE", f"/projects/{self.quote(project)}/members/{member_id}")

    def create_robot(self, project: str, name: str, permission: str, duration_days: int) -> tuple[dict[str, Any], str]:
        templates = {
            "pull": [("repository", "pull")],
            "build-push": [("repository", "pull"), ("repository", "push"), ("artifact", "read"), ("artifact", "create")],
            "project-admin": [("repository", "pull"), ("repository", "push"), ("repository", "delete"), ("artifact", "read"), ("artifact", "create"), ("artifact", "delete"), ("scan", "create")],
        }
        if permission not in templates:
            raise ValueError("unknown robot permission template")
        access = [{"resource": resource, "action": action} for resource, action in templates[permission]]
        response = self.request("POST", "/robots", {"name": name, "description": f"RE8CH {permission} robot for {project}", "disable": False, "duration": duration_days, "level": "project", "permissions": [{"kind": "project", "namespace": project, "access": access}]}, sanitize_response=False)
        token = str(response.get("secret") or response.get("token") or "")
        if not token:
            raise HarborError(502, "Harbor did not return the robot secret")
        return sanitized(response), token

    def disable_robot(self, robot_id: int, disabled: bool = True) -> None:
        self.request("PUT", f"/robots/{robot_id}", {"disable": disabled})

    def scan_artifact(self, project: str, repository: str, reference: str) -> None:
        self.request("POST", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/scan")

    def delete_artifact(self, project: str, repository: str, reference: str) -> None:
        self.request("DELETE", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}")

    def create_tag(self, project: str, repository: str, reference: str, tag: str) -> None:
        if not NAME_RE.fullmatch(tag):
            raise ValueError("invalid OCI tag")
        self.request("POST", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/tags", {"name": tag})

    def delete_tag(self, project: str, repository: str, reference: str, tag: str) -> None:
        self.request("DELETE", f"/projects/{self.quote(project)}/repositories/{self.quote(repository)}/artifacts/{self.quote(reference)}/tags/{self.quote(tag)}")


class KubernetesCredentialStore:
    """Stores material in Kubernetes while exposing only opaque references."""

    def __init__(self, namespace: str, kubectl: str = "kubectl", kubeconfig: str | None = None):
        self.namespace = namespace
        self.prefix = [kubectl]
        if kubeconfig:
            self.prefix += ["--kubeconfig", kubeconfig]

    @staticmethod
    def parse_reference(reference: str) -> tuple[str, str]:
        match = re.fullmatch(r"credential://harbor/([a-z0-9][a-z0-9._-]*)/([a-f0-9]{32})", reference)
        if not match:
            raise PermissionError("invalid Harbor credential reference")
        return match.group(1), match.group(2)

    def put(self, tenant: str, project: str, kind: str, material: dict[str, str]) -> str:
        if not NAME_RE.fullmatch(tenant) or not NAME_RE.fullmatch(project) or kind not in {"human", "robot", "dockerconfig"}:
            raise ValueError("invalid credential metadata")
        credential_id = uuid.uuid4().hex
        name = f"harbor-{credential_id}"
        manifest = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": self.namespace, "labels": {"credentials.re8ch.com/provider": "harbor", "credentials.re8ch.com/tenant": tenant, "credentials.re8ch.com/project": project, "credentials.re8ch.com/type": kind}}, "type": "Opaque", "stringData": material}
        proc = subprocess.run(self.prefix + ["apply", "-f", "-"], input=json.dumps(manifest).encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        if proc.returncode:
            raise RuntimeError("credential store write failed")
        return f"credential://harbor/{tenant}/{credential_id}"

    def get(self, reference: str, tenant: str) -> dict[str, str]:
        ref_tenant, credential_id = self.parse_reference(reference)
        if not hmac.compare_digest(ref_tenant, tenant):
            raise PermissionError("cross-tenant credential reference denied")
        proc = subprocess.run(self.prefix + ["--namespace", self.namespace, "get", "secret", f"harbor-{credential_id}", "-o", "json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        if proc.returncode:
            raise KeyError("credential reference not found")
        secret = json.loads(proc.stdout)
        labels = secret.get("metadata", {}).get("labels", {})
        if labels.get("credentials.re8ch.com/tenant") != tenant:
            raise PermissionError("credential tenant label mismatch")
        return {key: base64.b64decode(value).decode() for key, value in (secret.get("data") or {}).items()}

    def inject_dockerconfig(self, reference: str, tenant: str, namespace: str, name: str,
                            registry: str) -> dict[str, str]:
        if namespace != tenant or not NAME_RE.fullmatch(name):
            raise PermissionError("credential injection is limited to the tenant namespace")
        material = self.get(reference, tenant)
        username, password = material.get("username", ""), material.get("password", "")
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        dockerconfig = json.dumps({"auths": {registry: {"username": username, "password": password, "auth": auth}}}, separators=(",", ":"))
        manifest = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace, "labels": {"credentials.re8ch.com/provider": "harbor", "credentials.re8ch.com/tenant": tenant}}, "type": "kubernetes.io/dockerconfigjson", "stringData": {".dockerconfigjson": dockerconfig}}
        proc = subprocess.run(self.prefix + ["apply", "-f", "-"], input=json.dumps(manifest).encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        if proc.returncode:
            raise RuntimeError("docker credential injection failed")
        return {"namespace": namespace, "secret": name, "credential_material_returned": False}


class RedemptionRegistry:
    def __init__(self, path: Path, public_origin: str, ttl_seconds: int = 600):
        self.path = path
        self.public_origin = public_origin.rstrip("/")
        self.ttl_seconds = ttl_seconds
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS credential_redemptions (token_hash TEXT PRIMARY KEY, credential_ref TEXT NOT NULL, tenant TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS credential_adoptions (token_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER)")

    def issue(self, reference: str, tenant: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(48)
        expires_at = int(time.time()) + self.ttl_seconds
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO credential_redemptions VALUES (?,?,?,?,NULL)", (hashlib.sha256(token.encode()).hexdigest(), reference, tenant, expires_at))
        return {"credential_ref": reference, "redemption_url": f"{self.public_origin}/credentials/redeem#{token}", "expires_at": expires_at, "single_use": True}

    def consume(self, token: str) -> tuple[str, str] | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        current = int(time.time())
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT credential_ref,tenant,expires_at,consumed_at FROM credential_redemptions WHERE token_hash=?", (digest,)).fetchone()
            if not row or row[2] < current or row[3] is not None:
                return None
            updated = db.execute("UPDATE credential_redemptions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL", (current, digest)).rowcount
            return (row[0], row[1]) if updated == 1 else None

    def issue_adoption(self, tenant: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(48)
        expires_at = int(time.time()) + self.ttl_seconds
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO credential_adoptions VALUES (?,?,?,NULL)", (hashlib.sha256(token.encode()).hexdigest(), tenant, expires_at))
        return {"adoption_url": f"{self.public_origin}/credentials/adopt#{token}", "expires_at": expires_at, "single_use": True}

    def consume_adoption(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        current = int(time.time())
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT tenant,expires_at,consumed_at FROM credential_adoptions WHERE token_hash=?", (digest,)).fetchone()
            if not row or row[1] < current or row[2] is not None:
                return None
            updated = db.execute("UPDATE credential_adoptions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL", (current, digest)).rowcount
            return row[0] if updated == 1 else None


def desired_tenant(catalog: str, declared: dict[str, Any] | None = None) -> dict[str, str]:
    declared = declared or {}
    harbor = declared.get("harbor") or {}
    project = str(harbor.get("project") or catalog)
    username = str(harbor.get("webUsername") or catalog)
    email = str(harbor.get("accountEmail") or f"{catalog}@re8ch.com")
    for value in (catalog, project, username):
        if not NAME_RE.fullmatch(value):
            raise ValueError("invalid tenant Harbor name")
    return {"tenant": catalog, "project": project, "username": username, "email": email, "role": "projectAdmin"}
