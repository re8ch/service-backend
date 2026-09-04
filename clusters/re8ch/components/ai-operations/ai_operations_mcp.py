#!/usr/bin/env python3
"""RE8CH three-plane MCP server with Dex activation and permanent device tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from harbor_integration import HarborClient, HarborError, KubernetesCredentialStore, RedemptionRegistry, desired_tenant
from tenant_infrastructure import ServiceError, ServiceStore, catalog as service_catalog
from grafana_exploration import (
    RESULTS as GRAFANA_RESULTS,
    ExplorationError,
    GrafanaClient as TenantGrafanaClient,
    analyze_series,
    datasource_capability,
    execute_query as execute_grafana_query,
    frames_to_series,
    indicators as calculate_grafana_indicators,
    lag_correlations,
    stable_hash,
    tenant_config as grafana_tenant_config,
)

PROFILE = os.getenv("RE8CH_TOOL_PROFILE", "cloud").lower()
AUDIENCE = os.getenv("RE8CH_TOOL_AUDIENCE", f"re8ch-{PROFILE}-tool")
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "19443"))
DEX_USERINFO_URL = os.getenv("DEX_USERINFO_URL", "https://dex.re8ch.com/userinfo")
REGISTRY_PATH = Path(os.getenv("DEVICE_REGISTRY_PATH", "/var/lib/re8ch-ai-operations/devices.db"))
NODE_INVENTORY_PATH = Path(os.getenv("NODE_INVENTORY_PATH", "/etc/re8ch-ai-operations/nodes.json"))
KUBECONFIG_DIR = Path(os.getenv("KUBECONFIG_DIR", "/etc/re8ch-ai-operations/kubeconfigs"))
SSH_CONFIG = os.getenv("SSH_CONFIG", "/etc/re8ch-ai-operations/ssh_config")
SSH_IDENTITY_FILE = os.getenv("SSH_IDENTITY_FILE", "/etc/re8ch-ai-operations/id_ed25519")
MAX_TIMEOUT = int(os.getenv("MAX_COMMAND_TIMEOUT_SECONDS", "3600"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT_BYTES", str(4 * 1024 * 1024)))
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "re8ch:k8s-admins")
SERVICE_API_TOKEN = os.getenv("SERVICE_API_TOKEN", "")
HARBOR_URL = os.getenv("HARBOR_URL", "https://registry.re8ch.com")
GRAFANA_URL = os.getenv("GRAFANA_URL", "https://grafana.re8ch.com")
HARBOR_TENANTS_PATH = Path(os.getenv("HARBOR_TENANTS_PATH", "/etc/re8ch-ai-operations/harbor-tenants.json"))
CREDENTIAL_NAMESPACE = os.getenv("CREDENTIAL_NAMESPACE", "credential-broker")
TENANT_SERVICE_DB = Path(os.getenv("TENANT_SERVICE_DB", "/var/lib/re8ch-ai-operations/tenant-services.db"))

PROFILE_GROUPS = {
    "cloud": {ADMIN_GROUP},
    "infra": {ADMIN_GROUP, "re8ch:infra-operators"},
    "orchestra": {ADMIN_GROUP, "re8ch:agent-operators"},
    "all": {ADMIN_GROUP, "re8ch:infra-operators", "re8ch:agent-operators"},
    "tenant": {ADMIN_GROUP},
    "tenant-infra": set(),
    "tenant-admin": {"re8ch:tenant-admins"},
    "tenant-registry": set(),
    "tenant-observability": set(),
    "tenant-byoc": set(),
}
ALLOW_AUTHENTICATED_DEX = os.getenv("ALLOW_AUTHENTICATED_DEX", "false").lower() == "true"

OPERATIONS: dict[str, dict[str, Any]] = {}
SESSIONS: dict[str, subprocess.Popen[bytes]] = {}
SESSION_LOCK = threading.Lock()
INSTALL_STATES: dict[str, tuple[str, int, str]] = {}
INSTALL_STATE_LOCK = threading.Lock()
DEX_AUTH_URL = os.getenv("DEX_AUTH_URL", "https://dex.re8ch.com/auth")
DEX_TOKEN_URL = os.getenv("DEX_TOKEN_URL", "https://dex.re8ch.com/token")
INSTALL_CLIENT_ID = os.getenv("INSTALL_CLIENT_ID", "re8ch-marketplace-installer")
INSTALL_REDIRECT_URI = os.getenv("INSTALL_REDIRECT_URI", "https://marketplace.re8ch.com/install/callback")
INSTALL_STATE_HMAC_KEY = os.getenv("INSTALL_STATE_HMAC_KEY", "")
MARKETPLACE_SOURCE = os.getenv("MARKETPLACE_SOURCE", "https://github.com/re8ch/codex-admin-plugins.git")
CAPSULE_SIGNING_KEY = os.getenv("CAPSULE_SIGNING_KEY", "")
CAPSULE_POLICY_REVISION = int(os.getenv("CAPSULE_POLICY_REVISION", "1"))
CAPSULE_POLICY_PATH = Path(os.getenv("CAPSULE_POLICY_PATH", "/etc/re8ch-ai-operations/capsule-policy.json"))
CAPSULE_MAX_FILES_BYTES = int(os.getenv("CAPSULE_MAX_FILES_BYTES", str(8 * 1024 * 1024)))

CREDENTIAL_CATALOG = {
    "node-ssh": {"kind": "file", "paths": [SSH_CONFIG, SSH_IDENTITY_FILE], "actions": ["shell", "file"]},
    "kubernetes": {"kind": "file", "paths": [str(KUBECONFIG_DIR / "re8ch.yaml")], "actions": ["kubectl"]},
    "harbor-api": {"kind": "environment", "env": "HARBOR_ADMIN_PASSWORD", "actions": ["harbor-api"]},
    "grafana-api": {"kind": "kubernetes-secret", "namespace": "qianwen-ops", "name": "panel-grafana-admin-recovery", "keys": ["username", "password"], "actions": ["grafana-api"]},
    "litellm-api": {"kind": "environment", "env": "LITELLM_API_TOKEN", "actions": ["litellm-read"]},
    "github-native": {"kind": "environment", "env": "GH_TOKEN", "actions": ["github-api"]},
}


def now() -> int:
    return int(time.time())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def signed_install_state(nonce: str, issued_at: int) -> str:
    payload = f"{nonce}.{issued_at}"
    signature = hmac.new(INSTALL_STATE_HMAC_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{b64url(signature)}"


def verify_install_state(value: str) -> str | None:
    try:
        nonce, issued, _ = value.split(".", 2)
        issued_at = int(issued)
    except (ValueError, TypeError):
        return None
    if abs(now() - issued_at) > 600:
        return None
    return nonce if hmac.compare_digest(value, signed_install_state(nonce, issued_at)) else None


class DeviceRegistry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                  device_id TEXT PRIMARY KEY,
                  dex_subject TEXT NOT NULL,
                  groups_json TEXT NOT NULL,
                  audience TEXT NOT NULL,
                  token_hash TEXT NOT NULL UNIQUE,
                  public_key TEXT,
                  created_at INTEGER NOT NULL,
                  last_used_at INTEGER,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  deactivated_at INTEGER,
                  revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS registry_meta (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO registry_meta(id, revision) VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS capsules (
                  capsule_id TEXT PRIMARY KEY,
                  payload_json TEXT NOT NULL,
                  signature TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  executed_at INTEGER,
                  operation_id TEXT,
                  result_json TEXT
                );
                """
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def activate(self, subject: str, groups: list[str], public_key: str | None) -> tuple[dict[str, Any], str]:
        token = "r8d_" + secrets.token_urlsafe(48)
        device_id = "dev_" + uuid.uuid4().hex
        with self.lock, self.connect() as db:
            revision = db.execute("UPDATE registry_meta SET revision=revision+1 WHERE id=1 RETURNING revision").fetchone()[0]
            created = now()
            db.execute(
                "INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,1,NULL,?)",
                (device_id, subject, json.dumps(sorted(set(groups))), AUDIENCE, token_hash(token), public_key, created, None, revision),
            )
            db.commit()
        return {"device_id": device_id, "audience": AUDIENCE, "created_at": created, "enabled": True, "revision": revision}, token

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token.startswith("r8d_"):
            return None
        digest = token_hash(token)
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM devices WHERE token_hash=? AND audience=? AND enabled=1", (digest, AUDIENCE)
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE devices SET last_used_at=? WHERE device_id=?", (now(), row["device_id"]))
            db.commit()
        return {"subject": row["dex_subject"], "groups": json.loads(row["groups_json"]), "device_id": row["device_id"], "auth_type": "device"}

    def deactivate(self, device_id: str, actor: str) -> dict[str, Any]:
        with self.lock, self.connect() as db:
            revision = db.execute("UPDATE registry_meta SET revision=revision+1 WHERE id=1 RETURNING revision").fetchone()[0]
            changed = db.execute(
                "UPDATE devices SET enabled=0,deactivated_at=?,revision=? WHERE device_id=? AND enabled=1",
                (now(), revision, device_id),
            ).rowcount
            db.commit()
        return {"device_id": device_id, "deactivated": bool(changed), "revision": revision, "actor": actor}

    def status(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT device_id,dex_subject,audience,created_at,last_used_at,enabled,deactivated_at,revision FROM devices WHERE device_id=?",
                (device_id,),
            ).fetchone()
        return dict(row) if row else None

    def store_capsule(self, payload: dict[str, Any], signature: str, subject: str) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO capsules(capsule_id,payload_json,signature,subject,status,created_at) VALUES (?,?,?,?,?,?)",
                (payload["capsule_id"], canonical_json(payload), signature, subject, "signed", payload["created_at"]),
            )
            db.commit()

    def consume_capsule(self, capsule_id: str, subject: str) -> dict[str, Any] | None:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM capsules WHERE capsule_id=? AND subject=?", (capsule_id, subject)).fetchone()
            if row is None or row["status"] != "signed":
                return None
            changed = db.execute(
                "UPDATE capsules SET status='running',executed_at=? WHERE capsule_id=? AND status='signed'",
                (now(), capsule_id),
            ).rowcount
            db.commit()
        return dict(row) if changed else None

    def finish_capsule(self, capsule_id: str, result: dict[str, Any]) -> None:
        status = "succeeded" if result.get("exit_code") == 0 else "failed"
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE capsules SET status=?,operation_id=?,result_json=? WHERE capsule_id=?",
                (status, result.get("operation_id"), canonical_json(result), capsule_id),
            )
            db.commit()

    def capsule_status(self, capsule_id: str, subject: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT capsule_id,status,created_at,executed_at,operation_id FROM capsules WHERE capsule_id=? AND subject=?",
                (capsule_id, subject),
            ).fetchone()
        return dict(row) if row else None


REGISTRY: DeviceRegistry | None = None


def registry() -> DeviceRegistry:
    global REGISTRY
    if REGISTRY is None:
        REGISTRY = DeviceRegistry(REGISTRY_PATH)
    return REGISTRY


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def capsule_signature(payload: dict[str, Any]) -> str:
    if not CAPSULE_SIGNING_KEY:
        raise RuntimeError("capsule signing key is not configured")
    digest = hmac.new(CAPSULE_SIGNING_KEY.encode(), canonical_json(payload).encode(), hashlib.sha256).digest()
    return b64url(digest)


def capsule_policy() -> dict[str, Any]:
    if not CAPSULE_POLICY_PATH.exists():
        return {"revision": CAPSULE_POLICY_REVISION, "rules": []}
    value = json.loads(CAPSULE_POLICY_PATH.read_text())
    if not isinstance(value.get("rules"), list):
        raise ValueError("capsule policy rules must be a list")
    return value


def authorize_capsule(identity: dict[str, Any], action: str, node: str, credential_refs: list[str]) -> dict[str, Any]:
    if not identity.get("subject"):
        raise PermissionError("device identity is required")
    target = resolve_node(node)
    policy = capsule_policy()
    groups = set(identity.get("groups") or [])
    for rule in policy["rules"]:
        required_groups = set(rule.get("groups") or [])
        if action not in rule.get("actions", []):
            continue
        if "*" not in rule.get("nodes", []) and target["id"] not in rule.get("nodes", []):
            continue
        if required_groups and not groups.intersection(required_groups):
            continue
        if not set(credential_refs).issubset(set(rule.get("credential_refs", []))):
            continue
        return {"policy_revision": int(policy.get("revision", CAPSULE_POLICY_REVISION)), "rule": rule["name"], "node": target["id"], "action": action, "authorized": True}
    raise PermissionError("authorization policy denied capsule")


class CredentialBroker:
    """Resolve credential references for runners without exposing credential material."""

    def resolve(self, refs: list[str], action: str) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for ref in refs:
            item = CREDENTIAL_CATALOG.get(ref)
            if not item or action not in item.get("actions", []):
                raise PermissionError(f"credential reference is not allowed for {action}")
            if item["kind"] == "file":
                if not all(Path(path).exists() for path in item["paths"]):
                    raise RuntimeError(f"credential reference is unavailable: {ref}")
                resolved[ref] = {"kind": "file", "paths": list(item["paths"])}
            elif item["kind"] == "environment":
                value = os.getenv(item["env"])
                if not value:
                    raise RuntimeError(f"credential reference is unavailable: {ref}")
                resolved[ref] = {"kind": "environment", "value": value}
            else:
                resolved[ref] = {"kind": item["kind"]}
        return resolved


class NodeRunner:
    def run(self, capsule: dict[str, Any], credentials: dict[str, Any]) -> dict[str, Any]:
        if capsule["action"] not in {"shell", "file"}:
            raise ValueError("node runner supports shell and file capsules")
        node = resolve_node(capsule["node"])
        for item in capsule.get("files") or []:
            path = item["remote_path"]
            if not path.startswith("/var/tmp/re8ch-capsules/") or ".." in Path(path).parts:
                raise ValueError("capsule files must target /var/tmp/re8ch-capsules")
            data = base64.b64decode(item["data_base64"], validate=True)
            upload = f"install -d -m 700 {shlex.quote(str(Path(path).parent))} && base64 -d > {shlex.quote(path)}"
            result = command_result(ssh_argv(node, upload), base64.b64encode(data).decode(), 300)
            if result["exit_code"] != 0:
                return result
        return command_result(ssh_argv(node, capsule.get("command") or "true"), None, MAX_TIMEOUT)


def credential_catalog() -> list[dict[str, Any]]:
    values = []
    for name, item in CREDENTIAL_CATALOG.items():
        available = True
        if item["kind"] == "file":
            available = all(Path(path).exists() for path in item["paths"])
        elif item["kind"] == "environment":
            available = bool(os.getenv(item["env"]))
        elif item["kind"] == "kubernetes-secret":
            command = ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), "--namespace", item["namespace"], "get", "secret", item["name"]]
            available = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False).returncode == 0
        values.append({"name": name, "kind": item["kind"], "actions": item["actions"], "available": available})
    return values


def create_capsule(identity: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    refs = sorted(set(args.get("credential_refs") or []))
    policy = authorize_capsule(identity, args["action"], args["node"], refs)
    files = args.get("files") or []
    total = sum(len(base64.b64decode(item["data_base64"], validate=True)) for item in files)
    if total > CAPSULE_MAX_FILES_BYTES:
        raise ValueError("capsule files exceed maximum size")
    payload = {
        "version": 1,
        "capsule_id": "cap_" + uuid.uuid4().hex,
        "nonce": secrets.token_urlsafe(24),
        "created_at": now(),
        "subject": identity["subject"],
        "device_id": identity.get("device_id"),
        "policy_revision": policy["policy_revision"],
        "node": policy["node"],
        "action": args["action"],
        "command": args.get("command", ""),
        "credential_refs": refs,
        "files": files,
    }
    signature = capsule_signature(payload)
    registry().store_capsule(payload, signature, identity["subject"])
    return {"capsule": payload, "signature": signature, "status": "signed", "replay": "one-time"}


def execute_capsule(identity: dict[str, Any], capsule: dict[str, Any], signature: str) -> dict[str, Any]:
    if capsule.get("subject") != identity.get("subject"):
        raise PermissionError("capsule identity mismatch")
    if not hmac.compare_digest(signature, capsule_signature(capsule)):
        raise PermissionError("capsule signature is invalid")
    decision = authorize_capsule(identity, capsule["action"], capsule["node"], capsule.get("credential_refs") or [])
    credentials = CredentialBroker().resolve(capsule.get("credential_refs") or [], capsule["action"])
    saved = registry().consume_capsule(capsule["capsule_id"], identity["subject"])
    if not saved:
        raise PermissionError("capsule is unknown or already consumed")
    result = NodeRunner().run(capsule, credentials)
    registry().finish_capsule(capsule["capsule_id"], result)
    return {"capsule_id": capsule["capsule_id"], "runner": "ssh-node-runner", "policy_revision": capsule["policy_revision"], "policy_rule": decision["rule"], "credential_refs": sorted(credentials), "credential_material_exposed": False, **result}


def dex_identity(token: str) -> dict[str, Any] | None:
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        token_audience = claims.get("aud", [])
        audiences = {token_audience} if isinstance(token_audience, str) else set(token_audience)
    except Exception:
        return None
    if AUDIENCE not in audiences:
        return None
    request = urllib.request.Request(DEX_USERINFO_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.load(response)
    except (urllib.error.URLError, ValueError):
        return None
    subject = data.get("sub")
    groups = data.get("groups") or []
    if not subject or not isinstance(groups, list):
        return None
    tenant = data.get("tenant") or data.get("tenantRef")
    return {"subject": subject, "groups": groups, "tenant": tenant, "auth_type": "dex"}


def allowed(identity: dict[str, Any]) -> bool:
    if ALLOW_AUTHENTICATED_DEX and identity.get("auth_type") in {"dex", "device"} and identity.get("subject"):
        return True
    return bool(set(identity.get("groups", [])) & PROFILE_GROUPS.get(PROFILE, set()))


def command_result(argv: list[str], stdin: str | None, timeout: int) -> dict[str, Any]:
    operation_id = "op_" + uuid.uuid4().hex
    started = now()
    timeout = max(1, min(timeout, MAX_TIMEOUT))
    try:
        proc = subprocess.run(
            argv,
            input=stdin.encode() if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        result = {
            "operation_id": operation_id,
            "argv": argv,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[:MAX_OUTPUT].decode(errors="replace"),
            "stderr": proc.stderr[:MAX_OUTPUT].decode(errors="replace"),
            "truncated": len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT,
            "started_at": started,
            "finished_at": now(),
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "operation_id": operation_id,
            "argv": argv,
            "exit_code": 124,
            "stdout": (exc.stdout or b"")[:MAX_OUTPUT].decode(errors="replace"),
            "stderr": "command timed out",
            "truncated": False,
            "started_at": started,
            "finished_at": now(),
        }
    OPERATIONS[operation_id] = result
    return result


def inventory() -> list[dict[str, Any]]:
    return json.loads(NODE_INVENTORY_PATH.read_text()) if NODE_INVENTORY_PATH.exists() else []


def resolve_node(node: str) -> dict[str, Any]:
    for entry in inventory():
        if entry.get("id") == node and entry.get("enabled", True):
            return entry
    raise ValueError("unknown or disabled node")


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        value["required"] = required
    return value


COMMON_TOOLS = [
    {"name": "activate_device", "description": "Exchange the current Dex login for a permanent device token that remains valid until deactivation.", "inputSchema": schema({"public_key": {"type": "string"}})},
    {"name": "deactivate_device", "description": "Permanently deactivate a device token by id.", "inputSchema": schema({"device_id": {"type": "string"}}, ["device_id"])},
    {"name": "device_status", "description": "Read non-secret device activation status.", "inputSchema": schema({"device_id": {"type": "string"}}, ["device_id"])},
    {"name": "operation_status", "description": "Read a completed operation result.", "inputSchema": schema({"operation_id": {"type": "string"}}, ["operation_id"])},
]

CLOUD_TOOLS = [
    {"name": "cluster_contexts", "description": "List configured Kubernetes contexts without returning kubeconfig data.", "inputSchema": schema({})},
    {"name": "nodes", "description": "List SSH node inventory and reachability metadata.", "inputSchema": schema({})},
    {"name": "kubectl_exec", "description": "Run kubectl with complete argv and optional stdin against a configured context.", "inputSchema": schema({"context": {"type": "string"}, "namespace": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "stdin": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["context", "argv"])},
    {"name": "ssh_exec", "description": "Execute a command on an inventory node through server-held SSH credentials.", "inputSchema": schema({"node": {"type": "string"}, "command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["node", "command"])},
    {"name": "ssh_session_start", "description": "Start a long-running SSH command session.", "inputSchema": schema({"node": {"type": "string"}, "command": {"type": "string"}}, ["node", "command"])},
    {"name": "ssh_session_poll", "description": "Poll a long-running SSH session.", "inputSchema": schema({"session_id": {"type": "string"}}, ["session_id"])},
    {"name": "ssh_session_write", "description": "Write base64 data to an SSH session stdin.", "inputSchema": schema({"session_id": {"type": "string"}, "data_base64": {"type": "string"}}, ["session_id", "data_base64"])},
    {"name": "ssh_session_terminate", "description": "Terminate an SSH session.", "inputSchema": schema({"session_id": {"type": "string"}}, ["session_id"])},
    {"name": "ssh_upload", "description": "Upload base64 file content to a node path.", "inputSchema": schema({"node": {"type": "string"}, "remote_path": {"type": "string"}, "data_base64": {"type": "string"}}, ["node", "remote_path", "data_base64"])},
    {"name": "ssh_download", "description": "Download a node file as base64.", "inputSchema": schema({"node": {"type": "string"}, "remote_path": {"type": "string"}}, ["node", "remote_path"])},
    {"name": "credential_catalog", "description": "List non-secret credential references, availability, and supported actions. Never returns credential material.", "inputSchema": schema({})},
    {"name": "ops_capsule_create", "description": "Authorize and sign a one-time node operation capsule bound to the current device identity.", "inputSchema": schema({"action": {"type": "string", "enum": ["shell", "file"]}, "node": {"type": "string"}, "command": {"type": "string"}, "credential_refs": {"type": "array", "items": {"type": "string"}}, "files": {"type": "array", "items": {"type": "object", "properties": {"remote_path": {"type": "string"}, "data_base64": {"type": "string"}}, "required": ["remote_path", "data_base64"], "additionalProperties": False}}}, ["action", "node"])},
    {"name": "ops_capsule_execute", "description": "Verify, atomically consume, and execute a signed capsule through the node runner.", "inputSchema": schema({"capsule": {"type": "object"}, "signature": {"type": "string"}}, ["capsule", "signature"])},
    {"name": "ops_capsule_run", "description": "Create and immediately execute a signed one-time node operation capsule.", "inputSchema": schema({"action": {"type": "string", "enum": ["shell", "file"]}, "node": {"type": "string"}, "command": {"type": "string"}, "credential_refs": {"type": "array", "items": {"type": "string"}}, "files": {"type": "array", "items": {"type": "object", "properties": {"remote_path": {"type": "string"}, "data_base64": {"type": "string"}}, "required": ["remote_path", "data_base64"], "additionalProperties": False}}}, ["action", "node"])},
    {"name": "ops_capsule_status", "description": "Read capsule lifecycle status without returning payload or credentials.", "inputSchema": schema({"capsule_id": {"type": "string"}}, ["capsule_id"])},
]

INFRA_TOOLS = [
    {"name": "infra_observe", "description": "Run the reviewed observation command for an infrastructure domain.", "inputSchema": schema({"domain": {"type": "string", "enum": ["cilium", "ceph", "pigsty", "harbor", "grafana", "github", "litellm"]}}, ["domain"])},
    {"name": "infra_execute", "description": "Run an authorized infrastructure command using server-side credentials.", "inputSchema": schema({"domain": {"type": "string", "enum": ["cilium", "ceph", "pigsty", "harbor", "grafana", "github"]}, "argv": {"type": "array", "items": {"type": "string"}}, "stdin": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["domain", "argv"])},
    {"name": "harbor_users", "description": "List sanitized Harbor users through the native v2 API.", "inputSchema": schema({})},
    {"name": "harbor_projects", "description": "List sanitized Harbor projects through the native v2 API.", "inputSchema": schema({})},
    {"name": "harbor_robots", "description": "List sanitized Harbor robot accounts through the native v2 API.", "inputSchema": schema({})},
    {"name": "harbor_repositories", "description": "List repositories in one Harbor project.", "inputSchema": schema({"project": {"type": "string"}}, ["project"])},
    {"name": "harbor_artifacts", "description": "List artifacts and scan summaries in one repository.", "inputSchema": schema({"project": {"type": "string"}, "repository": {"type": "string"}}, ["project", "repository"])},
    {"name": "harbor_tenant_reconcile", "description": "Preview or apply one catalog tenant's private project, shared human account and Project Admin membership.", "inputSchema": schema({"tenant": {"type": "string"}, "apply": {"type": "boolean"}}, ["tenant"])},
    {"name": "harbor_user_update", "description": "Update a non-admin Harbor user's email and real name.", "inputSchema": schema({"user_id": {"type": "integer", "minimum": 1}, "email": {"type": "string"}, "realname": {"type": "string"}, "apply": {"type": "boolean"}}, ["user_id", "email", "realname"])},
    {"name": "harbor_tenant_password_rotate", "description": "Rotate one catalog tenant's shared user password into the broker and issue a one-time redemption URL.", "inputSchema": schema({"tenant": {"type": "string"}, "apply": {"type": "boolean"}}, ["tenant"])},
    {"name": "harbor_tenant_adoption_create", "description": "Issue a one-time HTTPS form for importing an existing catalog tenant Harbor password without passing it through MCP.", "inputSchema": schema({"tenant": {"type": "string"}}, ["tenant"])},
    {"name": "harbor_tenant_suspend", "description": "Suspend one tenant by removing its shared project membership and disabling project robots without deleting images.", "inputSchema": schema({"tenant": {"type": "string"}, "apply": {"type": "boolean"}}, ["tenant"])},
    {"name": "harbor_project_member_remove", "description": "Remove a Harbor project member without deleting the user or project.", "inputSchema": schema({"project": {"type": "string"}, "member_id": {"type": "integer", "minimum": 1}, "apply": {"type": "boolean"}}, ["project", "member_id"])},
    {"name": "harbor_robot_disable", "description": "Disable or enable a Harbor robot account.", "inputSchema": schema({"robot_id": {"type": "integer", "minimum": 1}, "disabled": {"type": "boolean"}, "apply": {"type": "boolean"}}, ["robot_id", "disabled"])},
    {"name": "harbor_artifact_scan", "description": "Request a Harbor vulnerability scan for one artifact.", "inputSchema": schema({"project": {"type": "string"}, "repository": {"type": "string"}, "reference": {"type": "string"}, "apply": {"type": "boolean"}}, ["project", "repository", "reference"])},
    {"name": "harbor_artifact_delete", "description": "Delete one explicitly selected Harbor artifact.", "inputSchema": schema({"project": {"type": "string"}, "repository": {"type": "string"}, "reference": {"type": "string"}, "apply": {"type": "boolean"}}, ["project", "repository", "reference"])},
    {"name": "grafana_inventory", "description": "Read sanitized Grafana organizations, folders, dashboards and datasource metadata through the native API.", "inputSchema": schema({})},
    {"name": "grafana_organization_get", "description": "Read one Grafana organization and its users without returning credentials.", "inputSchema": schema({"organization_id": {"type": "integer", "minimum": 1}}, ["organization_id"])},
    {"name": "kubernetes_workload_inventory", "description": "Read sanitized Kubernetes workload readiness for Headlamp-compatible control-plane views.", "inputSchema": schema({"namespace": {"type": "string"}})},
    {"name": "kubernetes_recent_events", "description": "Read recent Kubernetes warning events for a namespace or the complete cluster.", "inputSchema": schema({"namespace": {"type": "string"}})},
    {"name": "litellm_gateway_status", "description": "Read the cluster LiteLLM liveness and readiness endpoints without exposing credentials.", "inputSchema": schema({})},
    {"name": "litellm_models", "description": "List models exposed by the cluster LiteLLM gateway using its server-held credential.", "inputSchema": schema({})},
    {"name": "litellm_spend", "description": "Read LiteLLM aggregate spend data using its server-held credential.", "inputSchema": schema({})},
    {"name": "mihomos_cluster_status", "description": "Return the sanitized Mihomos provider/consumer status used by the Headlamp Mihomos Cluster plugin.", "inputSchema": schema({})},
    {"name": "public_edge_status", "description": "Return PublicEdge candidates and accepted public HTTPRoutes without changing DNS or routing.", "inputSchema": schema({})},
    {"name": "advanced_fabric_status", "description": "Return the sanitized node fabric status used by the Headlamp Advanced Fabrics plugin.", "inputSchema": schema({})},
    {"name": "cilium_helm_app_status", "description": "Return Cilium Helm release metadata and workload readiness without exposing Helm Secret payloads.", "inputSchema": schema({})},
    {"name": "rook_csi_app_status", "description": "Return Rook/Ceph CSI Helm release metadata and workload readiness without exposing Helm Secret payloads.", "inputSchema": schema({})},
    {"name": "mihomos_node_configure", "description": "Preview or apply constrained Mihomos node role labels.", "inputSchema": schema({"node": {"type": "string"}, "geo": {"type": "string", "enum": ["cn", "global"]}, "exit": {"type": "string", "enum": ["none", "cn", "global"]}, "apply": {"type": "boolean"}}, ["node", "geo", "exit"])},
    {"name": "mihomos_rule_configure", "description": "Preview or apply a validated domain-suffix rule before MATCH in a Mihomos node profile.", "inputSchema": schema({"profile": {"type": "string", "enum": ["cn", "global"]}, "domain_suffix": {"type": "string"}, "action": {"type": "string", "enum": ["DIRECT", "GLOBAL", "CN"]}, "present": {"type": "boolean"}, "apply": {"type": "boolean"}}, ["profile", "domain_suffix", "action"])},
    {"name": "advanced_fabric_node_configure", "description": "Preview or apply bounded AdvancedFabric per-node rate and traffic limits.", "inputSchema": schema({"node": {"type": "string"}, "uplink_mbps": {"type": "integer", "minimum": 1, "maximum": 100000}, "monthly_traffic_limit_gib": {"type": "integer", "minimum": 1, "maximum": 10000000}, "apply": {"type": "boolean"}}, ["node"])},
    {"name": "public_edge_candidate_configure", "description": "Preview or apply constrained PublicEdge candidate capacity, priority, lifecycle, and service classes.", "inputSchema": schema({"candidate": {"type": "string"}, "capacity_mbps": {"type": "integer", "minimum": 1, "maximum": 100000}, "priority": {"type": "integer", "minimum": -100000, "maximum": 100000}, "enabled": {"type": "boolean"}, "draining": {"type": "boolean"}, "service_classes": {"type": "array", "items": {"type": "string", "enum": ["api", "db-ro", "db-rw", "web", "registry"]}}, "apply": {"type": "boolean"}}, ["candidate"])},
    {"name": "public_edge_service_configure", "description": "Preview or register/update a public hostname and its ExternalDNS-only publication authority.", "inputSchema": schema({"hostname": {"type": "string"}, "service": {"type": "string"}, "service_class": {"type": "string", "enum": ["api", "db-ro", "db-rw", "web", "registry"]}, "probe_path": {"type": "string"}, "publication_namespace": {"type": "string"}, "publication_name": {"type": "string"}, "present": {"type": "boolean"}, "apply": {"type": "boolean"}}, ["hostname", "service", "service_class", "publication_namespace", "publication_name"])},
    {"name": "public_edge_candidate_policy_configure", "description": "Preview or apply bounded Candidate Selection scoring weights.", "inputSchema": schema({"capacity_weight": {"type": "integer", "minimum": 0, "maximum": 10000}, "local_area_bonus": {"type": "integer", "minimum": 0, "maximum": 10000000}, "priority_weight": {"type": "integer", "minimum": 0, "maximum": 10000}, "latency_divisor_ms": {"type": "integer", "minimum": 1, "maximum": 60000}, "latency_penalty_cap": {"type": "integer", "minimum": 0, "maximum": 100000}, "local_node_bonus": {"type": "integer", "minimum": 0, "maximum": 100000}, "apply": {"type": "boolean"}})},
    {"name": "cilium_helm_app_configure", "description": "Preview or apply a safe Cilium dynamic configuration field; does not change operator replica count.", "inputSchema": schema({"key": {"type": "string", "enum": ["debug", "enable-hubble", "enable-bgp-control-plane-status-report", "bpf-events-drop-enabled", "bpf-events-policy-verdict-enabled", "bpf-events-trace-enabled"]}, "value": {"type": "boolean"}, "apply": {"type": "boolean"}}, ["key", "value"])},
    {"name": "rook_csi_app_configure", "description": "Preview or apply a safe Rook operator configuration field.", "inputSchema": schema({"key": {"type": "string", "enum": ["ROOK_LOG_LEVEL", "ROOK_CEPH_COMMANDS_TIMEOUT_SECONDS", "ROOK_ENABLE_DISCOVERY_DAEMON"]}, "value": {"type": "string"}, "apply": {"type": "boolean"}}, ["key", "value"])},
]

TENANT_TOOLS = [
    {"name": "harbor_tenant_repositories", "description": "List repositories using a tenant-bound Harbor credential reference.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}}, ["tenant", "credential_ref"])},
    {"name": "harbor_tenant_artifacts", "description": "List artifacts in the credential-bound tenant project.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}, "repository": {"type": "string"}}, ["tenant", "credential_ref", "repository"])},
    {"name": "harbor_tenant_robot_create", "description": "Create a finite-duration project robot and store its secret without returning it.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}, "name": {"type": "string"}, "permission": {"type": "string", "enum": ["pull", "build-push", "project-admin"]}, "duration_days": {"type": "integer", "minimum": 1, "maximum": 365}, "apply": {"type": "boolean"}}, ["tenant", "credential_ref", "name", "permission"])},
    {"name": "harbor_tenant_robot_disable", "description": "Disable a robot in the credential-bound tenant project.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}, "robot_id": {"type": "integer", "minimum": 1}, "apply": {"type": "boolean"}}, ["tenant", "credential_ref", "robot_id"])},
    {"name": "harbor_tenant_kubernetes_inject", "description": "Inject a Harbor dockerconfig into the same-named tenant namespace without returning credential material.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}, "secret_name": {"type": "string"}, "apply": {"type": "boolean"}}, ["tenant", "credential_ref", "secret_name"])},
    {"name": "harbor_tenant_browser_launch", "description": "Issue a short-lived single-use credential URL for a controlled browser login without returning the password through MCP.", "inputSchema": schema({"tenant": {"type": "string"}, "credential_ref": {"type": "string"}}, ["tenant", "credential_ref"])},
]

TENANT_INFRA_TOOLS = [
    {"name": "tenant_create", "description": "Create a named tenant identity boundary. Protected admin identity is never created through this tool.", "inputSchema": schema({"tenant_ref": {"type": "string"}, "display_name": {"type": "string"}, "identity_ref": {"type": "string"}}, ["tenant_ref", "display_name", "identity_ref"])},
    {"name": "tenant_list", "description": "List tenant identity boundaries. Only the protected admin tenant may call this tool.", "inputSchema": schema({})},
    {"name": "tenant_grant_create", "description": "Map one published Consumable role to an app-native authorization adapter and return only an opaque access reference.", "inputSchema": schema({"tenant_ref": {"type": "string"}, "consumable_ref": {"type": "string"}, "role_ref": {"type": "string"}, "visibility": {"type": "string", "enum": ["Private", "Tenant", "Shared"]}, "constraints": {"type": "object"}}, ["tenant_ref", "consumable_ref", "role_ref", "visibility"])},
    {"name": "tenant_visibility_update", "description": "Change tenant visibility without exposing an application's native ACL model.", "inputSchema": schema({"tenant_ref": {"type": "string"}, "visibility": {"type": "string", "enum": ["Private", "Tenant", "Shared"]}}, ["tenant_ref", "visibility"])},
    {"name": "tenant_delete", "description": "Start deletion of a non-admin tenant and revoke its grants before adapter cleanup.", "inputSchema": schema({"tenant_ref": {"type": "string"}}, ["tenant_ref"])},
    {"name": "service_catalog_list", "description": "List tenant infrastructure service classes, plans, regions, parameters and meters.", "inputSchema": schema({})},
    {"name": "service_instance_plan", "description": "Validate and preview a tenant-scoped service instance without changing infrastructure.", "inputSchema": schema({"name": {"type": "string"}, "spec": {"type": "object"}}, ["name", "spec"])},
    {"name": "service_instance_create", "description": "Create a durable tenant service instance request idempotently.", "inputSchema": schema({"name": {"type": "string"}, "spec": {"type": "object"}, "idempotency_key": {"type": "string"}}, ["name", "spec", "idempotency_key"])},
    {"name": "service_instance_get", "description": "Read one service instance owned by the authenticated tenant.", "inputSchema": schema({"resource_ref": {"type": "string"}}, ["resource_ref"])},
    {"name": "service_instance_list", "description": "List service instances owned by the authenticated tenant.", "inputSchema": schema({})},
    {"name": "service_instance_update", "description": "Update the desired specification of a tenant service instance idempotently.", "inputSchema": schema({"resource_ref": {"type": "string"}, "spec": {"type": "object"}, "idempotency_key": {"type": "string"}}, ["resource_ref", "spec", "idempotency_key"])},
    {"name": "service_instance_suspend", "description": "Suspend a tenant service instance without deleting retained data.", "inputSchema": schema({"resource_ref": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["resource_ref", "idempotency_key"])},
    {"name": "service_instance_resume", "description": "Resume a suspended tenant service instance.", "inputSchema": schema({"resource_ref": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["resource_ref", "idempotency_key"])},
    {"name": "service_instance_delete", "description": "Soft-delete a tenant service instance and retain recoverable data for the controller retention period.", "inputSchema": schema({"resource_ref": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["resource_ref", "idempotency_key"])},
    {"name": "service_binding_create", "description": "Create an opaque tenant binding; credential material is never returned.", "inputSchema": schema({"resource_ref": {"type": "string"}, "binding_type": {"type": "string"}, "parameters": {"type": "object"}}, ["resource_ref", "binding_type"])},
    {"name": "service_binding_list", "description": "List bindings owned by the authenticated tenant.", "inputSchema": schema({"resource_ref": {"type": "string"}})},
    {"name": "service_binding_revoke", "description": "Revoke a tenant binding and its opaque credential reference.", "inputSchema": schema({"binding_ref": {"type": "string"}}, ["binding_ref"])},
    {"name": "usage_summary_get", "description": "Return unpriced usage totals for the authenticated tenant.", "inputSchema": schema({"resource_ref": {"type": "string"}})},
    {"name": "operation_get", "description": "Read an immutable tenant operation record.", "inputSchema": schema({"operation_id": {"type": "string"}}, ["operation_id"])},
]

EXPLORATION_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "timeRange": {"type": "object"}, "queries": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object"}},
        "pipeline": {"type": "array", "items": {"type": "object"}}, "indicators": {"type": "array", "items": {"type": "object"}},
        "visualization": {"type": "object"},
    },
    "required": ["queries"], "additionalProperties": False,
}

GRAFANA_TENANT_TOOLS = [
    {"name": "grafana_datasource_list", "description": "List query capabilities for datasources visible in the authenticated tenant Grafana organization.", "inputSchema": schema({})},
    {"name": "grafana_datasource_get", "description": "Read sanitized metadata for one tenant-visible Grafana datasource.", "inputSchema": schema({"datasource_uid": {"type": "string"}}, ["datasource_uid"])},
    {"name": "grafana_query", "description": "Run bounded tenant-scoped datasource queries through Grafana and return normalized DataFrames.", "inputSchema": schema({"spec": EXPLORATION_SPEC_SCHEMA}, ["spec"])},
    {"name": "grafana_analyze", "description": "Apply deterministic reduce, merge, math, normalize, or filter operations to numeric series.", "inputSchema": schema({
        "series": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "number"}}},
        "pipeline": {"type": "array", "items": {"type": "object"}}, "result_ref": {"type": "string"},
    })},
    {"name": "grafana_indicators", "description": "Calculate moving average, EMA, change rate, volatility, z-score, slope, trend, and quantiles.", "inputSchema": schema({"values": {"type": "array", "items": {"type": "number"}}, "indicators": {"type": "array", "items": {"type": "object"}}, "result_ref": {"type": "string"}}, ["indicators"])},
    {"name": "grafana_correlate", "description": "Calculate Pearson, Spearman, and lag correlations, or list native Grafana datasource correlations.", "inputSchema": schema({"left": {"type": "array", "items": {"type": "number"}}, "right": {"type": "array", "items": {"type": "number"}}, "method": {"type": "string", "enum": ["pearson", "spearman"]}, "max_lag": {"type": "integer", "minimum": 0, "maximum": 100}, "datasource_uid": {"type": "string"}})},
    {"name": "grafana_render", "description": "Render an existing tenant panel or an ephemeral dashboard to a short-lived PNG resource reference.", "inputSchema": schema({"dashboard_uid": {"type": "string"}, "dashboard": {"type": "object"}, "panel_id": {"type": "integer", "minimum": 1}, "from": {"type": ["string", "integer"]}, "to": {"type": ["string", "integer"]}, "width": {"type": "integer", "minimum": 320, "maximum": 2400}, "height": {"type": "integer", "minimum": 200, "maximum": 1600}}, ["panel_id"])},
    {"name": "grafana_exploration_save", "description": "Explicitly save an AI exploration dashboard in the authenticated tenant organization.", "inputSchema": schema({"dashboard": {"type": "object"}, "folder_uid": {"type": "string"}, "message": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["dashboard", "idempotency_key"])},
    {"name": "grafana_exploration_delete", "description": "Explicitly delete a saved AI exploration dashboard owned by the authenticated tenant.", "inputSchema": schema({"dashboard_uid": {"type": "string"}}, ["dashboard_uid"])},
]

TENANT_ADMIN_NAMES = {"tenant_create", "tenant_list", "tenant_grant_create", "tenant_visibility_update", "tenant_delete", "byoc_registration_list", "byoc_connection_approve", "byoc_connection_revoke"}
TENANT_CONSUMER_NAMES = {"service_catalog_list", "service_instance_plan", "service_instance_create", "service_instance_get", "service_instance_list", "service_instance_update", "service_instance_suspend", "service_instance_resume", "service_instance_delete", "service_binding_create", "service_binding_list", "service_binding_revoke", "usage_summary_get", "operation_get"}
TENANT_BYOC_TOOLS = [
    {"name": "byoc_cluster_register", "description": "Register an outbound-only BYOC cluster identity for admin approval.", "inputSchema": schema({"cluster_uid": {"type": "string"}, "region_ref": {"type": "string"}, "issuer": {"type": "string"}, "public_key": {"type": "string"}, "capabilities": {"type": "array", "items": {"type": "string"}}}, ["cluster_uid", "region_ref", "issuer", "public_key"])},
    {"name": "byoc_registration_list", "description": "List cluster registrations visible to the current plane.", "inputSchema": schema({"tenant_ref": {"type": "string"}})},
    {"name": "byoc_connection_list", "description": "List authenticated cluster connections visible to the current tenant.", "inputSchema": schema({})},
    {"name": "byoc_connection_approve", "description": "Approve one pending registration and issue a workload identity reference.", "inputSchema": schema({"registration_ref": {"type": "string"}}, ["registration_ref"])},
    {"name": "byoc_connection_revoke", "description": "Revoke a cluster connection; offline clusters enter RevocationPending.", "inputSchema": schema({"tenant_ref": {"type": "string"}, "connection_ref": {"type": "string"}, "offline": {"type": "boolean"}}, ["tenant_ref", "connection_ref"])},
]

ORCHESTRA_TOOLS = [
    {"name": "orchestra_service_status", "description": "Read application service health.", "inputSchema": schema({"service": {"type": "string", "enum": ["teable", "litellm", "opensandbox", "kagent", "codex"]}}, ["service"])},
    {"name": "orchestra_request", "description": "Call an application API using a server-side service credential reference.", "inputSchema": schema({"service": {"type": "string", "enum": ["teable", "litellm", "opensandbox", "kagent", "codex"]}, "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "path": {"type": "string"}, "body": {}}, ["service", "method", "path"])},
    {"name": "link_execution", "description": "Validate and return the stable cross-system execution identity chain.", "inputSchema": schema({"task_id": {"type": "string"}, "kagent_session_id": {"type": "string"}, "trace_id": {"type": "string"}, "codex_thread_id": {"type": "string"}, "artifact_id": {"type": "string"}}, ["task_id"])},
]

ORCHESTRA_HEALTH_PATHS = {
    "teable": "/api/health",
    "litellm": "/health/liveliness",
    "opensandbox": "/health",
    "kagent": "/healthz",
    "codex": "/healthz",
}


def tools() -> list[dict[str, Any]]:
    if PROFILE == "all":
        return COMMON_TOOLS + CLOUD_TOOLS + INFRA_TOOLS + TENANT_TOOLS + TENANT_INFRA_TOOLS + ORCHESTRA_TOOLS
    if PROFILE == "tenant-infra":
        return TENANT_INFRA_TOOLS
    if PROFILE == "tenant-admin":
        return [tool for tool in TENANT_INFRA_TOOLS if tool["name"] in TENANT_ADMIN_NAMES] + [tool for tool in TENANT_BYOC_TOOLS if tool["name"] in TENANT_ADMIN_NAMES]
    if PROFILE in {"tenant-registry", "tenant-observability"}:
        base = [tool for tool in TENANT_INFRA_TOOLS if tool["name"] in TENANT_CONSUMER_NAMES]
        return base + (GRAFANA_TENANT_TOOLS if PROFILE == "tenant-observability" else [])
    if PROFILE == "tenant-byoc":
        return [tool for tool in TENANT_BYOC_TOOLS if tool["name"] in {"byoc_cluster_register", "byoc_registration_list", "byoc_connection_list", "byoc_connection_revoke"}]
    return COMMON_TOOLS + {"cloud": CLOUD_TOOLS, "infra": INFRA_TOOLS, "orchestra": ORCHESTRA_TOOLS, "tenant": TENANT_TOOLS, "tenant-infra": TENANT_INFRA_TOOLS}.get(PROFILE, [])


def harbor_admin_client() -> HarborClient:
    username = os.getenv("HARBOR_ADMIN_USERNAME", "")
    password = os.getenv("HARBOR_ADMIN_PASSWORD", "")
    if not username or not password:
        username = kubernetes_secret_value("ai-operations", "cluster-infra-harbor", "username")
        password = kubernetes_secret_value("ai-operations", "cluster-infra-harbor", "password")
    return HarborClient(HARBOR_URL, username, password)


def kubernetes_secret_value(namespace: str, name: str, key: str) -> str:
    """Read a server-held Secret without routing its value through operation logs or MCP output."""
    if not all(re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?", item) for item in (namespace, name, key)):
        raise ValueError("invalid Kubernetes Secret reference")
    command = ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), "--namespace", namespace, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"credential reference is unavailable: {namespace}/{name}:{key}")
    try:
        return base64.b64decode(result.stdout, validate=True).decode()
    finally:
        result = None


def grafana_request(path: str, *, organization_id: int | None = None) -> Any:
    token = os.getenv("GRAFANA_API_TOKEN", "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        username = kubernetes_secret_value("qianwen-ops", "panel-grafana-admin-recovery", "username")
        password = kubernetes_secret_value("qianwen-ops", "panel-grafana-admin-recovery", "password")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    if organization_id:
        headers["X-Grafana-Org-Id"] = str(organization_id)
    request = urllib.request.Request(GRAFANA_URL.rstrip("/") + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def tenant_grafana_client(tenant: str, *, administrative: bool = False) -> TenantGrafanaClient:
    config = grafana_tenant_config(tenant)
    if administrative:
        username = os.getenv("GRAFANA_ADMIN_USERNAME", "")
        password = os.getenv("GRAFANA_ADMIN_PASSWORD", "")
        if not username or not password:
            username = kubernetes_secret_value("qianwen-ops", "panel-grafana-admin-recovery", "username")
            password = kubernetes_secret_value("qianwen-ops", "panel-grafana-admin-recovery", "password")
        authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    else:
        token = os.getenv(config.get("tokenEnv") or ("GRAFANA_TENANT_TOKEN_" + tenant.upper().replace("-", "_")), "")
        if not token:
            if not config.get("namespace") or not config.get("tokenSecret"):
                raise RuntimeError("tenant Grafana credential reference is unavailable")
            token = kubernetes_secret_value(config["namespace"], config["tokenSecret"], "token")
        authorization = "Bearer " + token
    return TenantGrafanaClient(GRAFANA_URL, authorization, int(config["orgId"]))


def tenant_grafana_datasources(client: TenantGrafanaClient) -> list[dict[str, Any]]:
    rows = client.request("GET", "/api/datasources")
    return [row for row in rows if row.get("uid") and datasource_capability(row)["queryKinds"]]


def sanitized_grafana_inventory() -> dict[str, Any]:
    health = grafana_request("/api/health")
    organizations = grafana_request("/api/orgs")
    folders = grafana_request("/api/folders?limit=1000")
    dashboards = grafana_request("/api/search?type=dash-db&limit=5000")
    datasources = grafana_request("/api/datasources")
    return {
        "health": {key: health.get(key) for key in ("database", "version", "commit")},
        "organizations": [{key: item.get(key) for key in ("id", "name")} for item in organizations],
        "folders": [{key: item.get(key) for key in ("id", "uid", "title", "url")} for item in folders],
        "dashboards": [{key: item.get(key) for key in ("id", "uid", "title", "folderUid", "folderTitle", "url")} for item in dashboards],
        "datasources": [{key: item.get(key) for key in ("id", "uid", "name", "type", "access", "isDefault", "readOnly")} for item in datasources],
        "credentialMaterialReturned": False,
    }


def credential_store() -> KubernetesCredentialStore:
    kubeconfig = str(KUBECONFIG_DIR / "re8ch.yaml") if (KUBECONFIG_DIR / "re8ch.yaml").exists() else None
    return KubernetesCredentialStore(CREDENTIAL_NAMESPACE, kubeconfig=kubeconfig)


def redemption_registry() -> RedemptionRegistry:
    return RedemptionRegistry(REGISTRY_PATH, os.getenv("PUBLIC_ORIGIN", "https://tools.re8ch.com"))


def tenant_catalog() -> dict[str, dict[str, Any]]:
    if not HARBOR_TENANTS_PATH.exists():
        return {}
    return json.loads(HARBOR_TENANTS_PATH.read_text()).get("tenants", {})


_TENANT_SERVICE_STORE: ServiceStore | None = None


def tenant_service_store() -> ServiceStore:
    global _TENANT_SERVICE_STORE
    if _TENANT_SERVICE_STORE is None:
        _TENANT_SERVICE_STORE = ServiceStore(TENANT_SERVICE_DB)
    return _TENANT_SERVICE_STORE


def tenant_catalog_for_profile() -> list[dict[str, Any]]:
    services = service_catalog()
    allowed = {
        "tenant-registry": {"registry.harbor.v1"},
        "tenant-observability": {"observability.grafana.v1"},
        "tenant-byoc": {"network.amongclusters.v1"},
    }.get(PROFILE)
    return [item for item in services if not allowed or item.get("serviceClass") in allowed]


def mcp_resources(identity: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize bounded, tenant-scoped MCP resources; samples remain outside Kubernetes/MCP."""
    tenant = authenticated_tenant(identity)
    store = tenant_service_store()
    records: list[tuple[str, str, Any]] = [
        (f"re8ch://tenant/{tenant}/catalog", "Service catalog", {"services": tenant_catalog_for_profile(), "pricingIncluded": False}),
    ]
    if PROFILE == "tenant-admin":
        records.extend([
            ("re8ch://admin/tenants", "Tenants", {"tenants": store.tenants()}),
            ("re8ch://admin/byoc/registrations", "BYOC registrations", {"registrations": store.registrations()}),
            ("re8ch://admin/byoc/connections", "BYOC connections", {"connections": store.connections()}),
        ])
    else:
        records.extend([
            (f"re8ch://tenant/{tenant}/instances", "Service instances", {"instances": store.list(tenant)}),
            (f"re8ch://tenant/{tenant}/bindings", "Consumption bindings", {"bindings": store.bindings(tenant)}),
            (f"re8ch://tenant/{tenant}/usage", "Unpriced usage summary", store.usage_summary(tenant, None)),
        ])
        if PROFILE == "tenant-byoc":
            records.extend([
                (f"re8ch://tenant/{tenant}/byoc/registrations", "BYOC registrations", {"registrations": store.registrations(tenant)}),
                (f"re8ch://tenant/{tenant}/byoc/connections", "BYOC connections", {"connections": store.connections(tenant)}),
            ])
    return [{"uri": uri, "name": name, "mimeType": "application/json", "value": value} for uri, name, value in records]


def resources_list(identity: dict[str, Any], cursor: str | None) -> dict[str, Any]:
    page_size = max(1, min(int(os.getenv("MCP_RESOURCE_PAGE_SIZE", "50")), 200))
    try:
        offset = int(cursor or "0")
    except ValueError as exc:
        raise ValueError("invalid resource cursor") from exc
    records = mcp_resources(identity)
    if offset < 0 or offset > len(records):
        raise ValueError("invalid resource cursor")
    page = records[offset:offset + page_size]
    result: dict[str, Any] = {"resources": [{key: item[key] for key in ("uri", "name", "mimeType")} for item in page]}
    if offset + page_size < len(records):
        result["nextCursor"] = str(offset + page_size)
    return result


def resources_read(identity: dict[str, Any], uri: str) -> dict[str, Any]:
    transient = re.fullmatch(r"re8ch://tenant/([^/]+)/grafana/results/(gr_[a-f0-9]+)", uri)
    if transient:
        tenant = authenticated_tenant(identity)
        if transient.group(1) != tenant:
            raise PermissionError("resource is not visible to the authenticated tenant")
        value = GRAFANA_RESULTS.get(tenant, transient.group(2))
        if isinstance(value, dict) and isinstance(value.get("blob"), (bytes, bytearray)):
            return {"contents": [{"uri": uri, "mimeType": value.get("mimeType", "application/octet-stream"), "blob": base64.b64encode(value["blob"]).decode()}]}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}]}
    match = next((item for item in mcp_resources(identity) if item["uri"] == uri), None)
    if not match:
        raise PermissionError("resource is not visible to the authenticated tenant")
    return {"contents": [{"uri": uri, "mimeType": match["mimeType"], "text": json.dumps(match["value"], ensure_ascii=False, separators=(",", ":"))}]}


def authenticated_tenant(identity: dict[str, Any]) -> str:
    explicit = str(identity.get("tenant") or "").strip().lower()
    groups = {str(group) for group in identity.get("groups") or []}
    if explicit and not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", explicit):
        raise PermissionError("authenticated tenant is not a valid DNS label")
    if explicit:
        if explicit == "admin" and "re8ch:tenant-admins" not in groups:
            raise PermissionError("admin tenant requires the protected tenant-admin identity")
        return explicit
    candidates = set()
    if "re8ch:tenant-admins" in groups:
        candidates.add("admin")
    for group in groups:
        match = re.fullmatch(r"re8ch:tenant:([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)", group)
        legacy = re.fullmatch(r"re8ch:([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)-(?:operators|members|tenant)", group)
        match = match or legacy
        if match:
            candidates.add(match.group(1))
    if len(candidates) != 1:
        raise PermissionError("exactly one authenticated tenant context is required")
    return candidates.pop()


def tenant_client(tenant: str, reference: str) -> tuple[HarborClient, str]:
    material = credential_store().get(reference, tenant)
    project = material.get("project", tenant)
    return HarborClient(HARBOR_URL, material.get("username", ""), material.get("password", "")), project


def litellm_get(path: str, authenticated: bool = True) -> dict[str, Any]:
    base = os.getenv("LITELLM_BASE_URL", "http://litellm.llm-gateway.svc.cluster.local:4000").rstrip("/")
    headers = {"Accept": "application/json"}
    if authenticated:
        token = os.getenv("LITELLM_API_TOKEN", "")
        if not token:
            raise ValueError("litellm-api credential is not configured")
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {"status": response.status, "body": response.read(MAX_OUTPUT).decode(errors="replace")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read(MAX_OUTPUT).decode(errors="replace")}


def kubectl_json(argv: list[str], timeout: int = 120) -> dict[str, Any]:
    command = ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), *argv, "-o", "json"]
    result = command_result(command, None, timeout)
    if result["exit_code"] != 0:
        return result
    try:
        return {"operation_id": result["operation_id"], "items": json.loads(result["stdout"]).get("items", [])}
    except (ValueError, AttributeError):
        return {**result, "parse_error": "Kubernetes response was not a list"}


def kubectl_object(argv: list[str], timeout: int = 120) -> tuple[dict[str, Any], str]:
    command = ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), *argv, "-o", "json"]
    result = command_result(command, None, timeout)
    if result["exit_code"] != 0:
        raise ValueError(result["stderr"] or "kubectl failed")
    return json.loads(result["stdout"]), result["operation_id"]


def kubectl_patch(argv: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    command = ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), *argv,
               "--type=merge", "--patch", json.dumps(payload, separators=(",", ":")), "-o", "json"]
    result = command_result(command, None, 120)
    if result["exit_code"] != 0:
        raise ValueError(result["stderr"] or "kubectl patch failed")
    return {"operation_id": result["operation_id"], "object": json.loads(result["stdout"])}


def mutation_preview(resource: str, before: Any, after: Any, apply: bool, rollback: Any) -> dict[str, Any]:
    return {"resource": resource, "applied": apply, "before": before, "after": after, "rollback": rollback,
            "safety": "explicit apply=true required; inputs are allowlisted and bounded"}


def valid_dns_label(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if len(value) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
        raise ValueError("invalid DNS name")
    return value


def configmap_json_status(namespace: str, selector: str) -> dict[str, Any]:
    result = kubectl_json(["--namespace", namespace, "get", "configmaps", "--selector", selector])
    statuses = []
    for item in result.get("items", []):
        raw = (item.get("data") or {}).get("status.json")
        if not raw:
            continue
        try:
            statuses.append(json.loads(raw))
        except ValueError:
            statuses.append({"name": item.get("metadata", {}).get("name"), "valid": False})
    return {"operation_id": result.get("operation_id"), "count": len(statuses), "statuses": statuses}


def helm_app_status(namespace: str, release: str, selectors: list[str]) -> dict[str, Any]:
    releases = kubectl_json(["--namespace", namespace, "get", "secrets", "--selector", f"owner=helm,name={release}"])
    workloads = []
    for selector in selectors:
        value = kubectl_json(["--namespace", namespace, "get", "deployments,statefulsets,daemonsets", "--selector", selector])
        for item in value.get("items", []):
            status = item.get("status") or {}
            workloads.append({
                "kind": item.get("kind"), "name": item.get("metadata", {}).get("name"),
                "desired": status.get("replicas", status.get("desiredNumberScheduled", 0)),
                "ready": status.get("readyReplicas", status.get("numberReady", 0)),
                "available": status.get("availableReplicas", status.get("numberAvailable", 0)),
            })
    revisions = [{"name": x.get("metadata", {}).get("name"), "version": x.get("metadata", {}).get("labels", {}).get("version"), "status": x.get("metadata", {}).get("labels", {}).get("status")} for x in releases.get("items", [])]
    return {"namespace": namespace, "release": release, "revisions": revisions, "workloads": workloads}


def ssh_argv(node: dict[str, Any], command: str) -> list[str]:
    return ["ssh", "-F", SSH_CONFIG, "--", node["target"], command]


def call_grafana_tenant_tool(name: str, args: dict[str, Any], identity: dict[str, Any]) -> Any:
    tenant = authenticated_tenant(identity)
    client = tenant_grafana_client(tenant)
    operation_id = "op_" + uuid.uuid4().hex
    datasources = tenant_grafana_datasources(client)
    if name == "grafana_datasource_list":
        return {"operationId": operation_id, "tenantRef": tenant, "datasources": [datasource_capability(row) for row in datasources]}
    if name == "grafana_datasource_get":
        row = next((row for row in datasources if row.get("uid") == args["datasource_uid"]), None)
        if not row:
            raise PermissionError("datasource is not visible to the authenticated tenant")
        return {"operationId": operation_id, "tenantRef": tenant, "datasource": datasource_capability(row)}
    if name == "grafana_query":
        return {"operationId": operation_id, "tenantRef": tenant, **execute_grafana_query(client, tenant, args["spec"], datasources)}
    if name in {"grafana_analyze", "grafana_indicators"}:
        series = args.get("series")
        if not series and args.get("result_ref"):
            series = frames_to_series(GRAFANA_RESULTS.get(tenant, args["result_ref"]))
        if not series and args.get("values"):
            series = {"input": args["values"]}
        if not series:
            raise ExplorationError("numeric series or result_ref is required")
        if name == "grafana_analyze":
            result = analyze_series(series, args.get("pipeline") or [])
        else:
            if len(series) != 1:
                raise ExplorationError("grafana_indicators requires exactly one numeric series")
            result = {"indicators": calculate_grafana_indicators(next(iter(series.values())), args["indicators"])}
        ref = GRAFANA_RESULTS.put(tenant, result)
        return {"operationId": operation_id, "tenantRef": tenant, "resultRef": ref, "resultUri": f"re8ch://tenant/{tenant}/grafana/results/{ref}", **result}
    if name == "grafana_correlate":
        if args.get("datasource_uid"):
            if not any(row.get("uid") == args["datasource_uid"] for row in datasources):
                raise PermissionError("datasource is not visible to the authenticated tenant")
            encoded = urllib.parse.quote(args["datasource_uid"], safe="")
            rows = client.request("GET", f"/api/datasources/uid/{encoded}/correlations")
            keys = ("uid", "label", "description", "type", "targetUID", "config")
            return {"operationId": operation_id, "tenantRef": tenant, "nativeCorrelations": [{key: row.get(key) for key in keys} for row in rows]}
        left, right = args.get("left") or [], args.get("right") or []
        if len(left) != len(right) or len(left) < 2:
            raise ExplorationError("left and right must have the same length of at least two")
        method = args.get("method", "pearson")
        rows = lag_correlations([float(x) for x in left], [float(x) for x in right], method, int(args.get("max_lag", 0)))
        best = max((row for row in rows if row["coefficient"] is not None), key=lambda row: abs(row["coefficient"]), default=None)
        return {"operationId": operation_id, "tenantRef": tenant, "method": method, "correlations": rows, "best": best, "warning": "Correlation does not imply causation."}
    admin = tenant_grafana_client(tenant, administrative=True)
    if name == "grafana_render":
        dashboard_uid = args.get("dashboard_uid")
        temporary_uid = None
        if args.get("dashboard"):
            temporary_uid = "mcp-render-" + uuid.uuid4().hex[:20]
            dashboard = dict(args["dashboard"])
            dashboard.update({"uid": temporary_uid, "tags": sorted(set(dashboard.get("tags") or []) | {"re8ch-ai-ephemeral", f"tenant:{tenant}"})})
            admin.request("POST", "/api/dashboards/db", {"dashboard": dashboard, "folderUid": "", "overwrite": False, "message": "Ephemeral tenant MCP render"})
            dashboard_uid = temporary_uid
        if not dashboard_uid:
            raise ExplorationError("dashboard_uid or dashboard is required")
        query = urllib.parse.urlencode({"panelId": args["panel_id"], "from": args.get("from", "now-1h"), "to": args.get("to", "now"), "width": args.get("width", 1000), "height": args.get("height", 500), "tz": "UTC"})
        uid = urllib.parse.quote(str(dashboard_uid), safe="")
        try:
            image = admin.request("GET", f"/render/d-solo/{uid}?{query}", timeout=60, raw=True)
        finally:
            if temporary_uid:
                admin.request("DELETE", f"/api/dashboards/uid/{temporary_uid}")
        ref = GRAFANA_RESULTS.put(tenant, {"blob": image, "mimeType": "image/png", "queryHash": stable_hash(args)})
        return {"operationId": operation_id, "tenantRef": tenant, "imageRef": f"re8ch://tenant/{tenant}/grafana/results/{ref}", "mimeType": "image/png", "expiresInSeconds": 900}
    if name == "grafana_exploration_save":
        dashboard = dict(args["dashboard"])
        dashboard.setdefault("uid", "ai-" + hashlib.sha256(f"{tenant}:{args['idempotency_key']}".encode()).hexdigest()[:24])
        dashboard["tags"] = sorted(set(dashboard.get("tags") or []) | {"re8ch-ai-exploration", f"tenant:{tenant}"})
        if dashboard.get("uid"):
            encoded = urllib.parse.quote(str(dashboard["uid"]), safe="")
            try:
                current = admin.request("GET", f"/api/dashboards/uid/{encoded}")
            except ExplorationError:
                current = None
            current_tags = set(((current or {}).get("dashboard") or {}).get("tags") or [])
            if current and {"re8ch-ai-exploration", f"tenant:{tenant}"} - current_tags:
                raise PermissionError("a provisioned or non-exploration dashboard cannot be overwritten")
        folder_uid = f"ai-explorations-{tenant}"
        try:
            admin.request("GET", f"/api/folders/{folder_uid}")
        except ExplorationError:
            admin.request("POST", "/api/folders", {"uid": folder_uid, "title": "AI Explorations"})
        saved = admin.request("POST", "/api/dashboards/db", {"dashboard": dashboard, "folderUid": folder_uid, "overwrite": True, "message": args.get("message", "Saved by tenant observability MCP")})
        return {"operationId": operation_id, "tenantRef": tenant, "dashboardUid": saved.get("uid"), "url": saved.get("url"), "idempotencyKey": args["idempotency_key"], "queryHash": stable_hash(dashboard)}
    uid = urllib.parse.quote(args["dashboard_uid"], safe="")
    existing = admin.request("GET", f"/api/dashboards/uid/{uid}")
    tags = set((existing.get("dashboard") or {}).get("tags") or [])
    if {"re8ch-ai-exploration", f"tenant:{tenant}"} - tags:
        raise PermissionError("only tenant-owned AI exploration dashboards may be deleted")
    deleted = admin.request("DELETE", f"/api/dashboards/uid/{uid}")
    return {"operationId": operation_id, "tenantRef": tenant, "deleted": True, "title": deleted.get("title")}


def call_tool(name: str, args: dict[str, Any], identity: dict[str, Any]) -> Any:
    if name not in {tool["name"] for tool in tools()}:
        raise PermissionError("tool is not published in this profile")
    if PROFILE == "tenant-observability" and name in {tool["name"] for tool in GRAFANA_TENANT_TOOLS}:
        return call_grafana_tenant_tool(name, args, identity)
    if name == "activate_device":
        if identity.get("auth_type") != "dex":
            raise PermissionError("device activation requires a live Dex access token")
        meta, token = registry().activate(identity["subject"], identity["groups"], args.get("public_key"))
        return {**meta, "device_token": token, "token_expires": None, "deactivation_required": True}
    if name == "deactivate_device":
        return registry().deactivate(args["device_id"], identity["subject"])
    if name == "device_status":
        return registry().status(args["device_id"])
    if name == "operation_status":
        return OPERATIONS.get(args["operation_id"], {"operation_id": args["operation_id"], "status": "unknown"})

    if PROFILE in {"cloud", "all"}:
        if name == "cluster_contexts":
            return [{"name": p.stem, "kubeconfig_ref": str(p)} for p in sorted(KUBECONFIG_DIR.glob("*.yaml"))]
        if name == "nodes":
            return [{k: v for k, v in n.items() if k not in {"private_key", "password"}} for n in inventory()]
        if name == "credential_catalog":
            return credential_catalog()
        if name == "ops_capsule_create":
            return create_capsule(identity, args)
        if name == "ops_capsule_execute":
            return execute_capsule(identity, args["capsule"], args["signature"])
        if name == "ops_capsule_run":
            signed = create_capsule(identity, args)
            return execute_capsule(identity, signed["capsule"], signed["signature"])
        if name == "ops_capsule_status":
            return registry().capsule_status(args["capsule_id"], identity["subject"]) or {"capsule_id": args["capsule_id"], "status": "unknown"}
        if name == "kubectl_exec":
            context = args["context"]
            path = KUBECONFIG_DIR / f"{context}.yaml"
            if not path.exists() or "/" in context or ".." in context:
                raise ValueError("unknown context")
            argv = ["kubectl", "--kubeconfig", str(path)]
            if args.get("namespace"):
                argv += ["--namespace", args["namespace"]]
            argv += args["argv"]
            return command_result(argv, args.get("stdin"), int(args.get("timeout_seconds", 300)))
        if name == "ssh_exec":
            return command_result(ssh_argv(resolve_node(args["node"]), args["command"]), None, int(args.get("timeout_seconds", 300)))
        if name == "ssh_upload":
            data = base64.b64decode(args["data_base64"], validate=True)
            return command_result(ssh_argv(resolve_node(args["node"]), f"base64 -d > {shlex.quote(args['remote_path'])}"), base64.b64encode(data).decode(), 300)
        if name == "ssh_download":
            result = command_result(ssh_argv(resolve_node(args["node"]), f"base64 < {shlex.quote(args['remote_path'])}"), None, 300)
            if result["exit_code"] == 0:
                result["data_base64"] = "".join(result.pop("stdout").split())
            return result
        if name == "ssh_session_start":
            session_id = "ses_" + uuid.uuid4().hex
            proc = subprocess.Popen(ssh_argv(resolve_node(args["node"]), args["command"]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            with SESSION_LOCK:
                SESSIONS[session_id] = proc
            return {"session_id": session_id, "pid": proc.pid, "status": "running"}
        if name == "ssh_session_write":
            with SESSION_LOCK:
                proc = SESSIONS.get(args["session_id"])
            if not proc or not proc.stdin:
                raise ValueError("unknown session")
            proc.stdin.write(base64.b64decode(args["data_base64"], validate=True)); proc.stdin.flush()
            return {"session_id": args["session_id"], "status": "written"}
        if name == "ssh_session_poll":
            with SESSION_LOCK:
                proc = SESSIONS.get(args["session_id"])
            if not proc:
                raise ValueError("unknown session")
            output = b""
            if proc.poll() is not None and proc.stdout:
                output = proc.stdout.read(MAX_OUTPUT)
            return {"session_id": args["session_id"], "running": proc.poll() is None, "exit_code": proc.poll(), "output": output.decode(errors="replace")}
        if name == "ssh_session_terminate":
            with SESSION_LOCK:
                proc = SESSIONS.pop(args["session_id"], None)
            if not proc:
                raise ValueError("unknown session")
            proc.terminate()
            return {"session_id": args["session_id"], "status": "terminated"}

    if PROFILE in {"infra", "all"}:
        if name == "grafana_inventory":
            return sanitized_grafana_inventory()
        if name == "grafana_organization_get":
            org_id = int(args["organization_id"])
            organization = grafana_request(f"/api/orgs/{org_id}")
            users = grafana_request(f"/api/orgs/{org_id}/users")
            return {
                "organization": {key: organization.get(key) for key in ("id", "name", "address")},
                "users": [{key: item.get(key) for key in ("orgId", "userId", "email", "login", "role")} for item in users],
                "credentialMaterialReturned": False,
            }
        if name == "kubernetes_workload_inventory":
            namespace = args.get("namespace")
            command = ["get", "deployments,statefulsets,daemonsets,jobs,cronjobs"]
            command += ["--namespace", namespace] if namespace else ["--all-namespaces"]
            result = kubectl_json(command)
            workloads = []
            for item in result.get("items", []):
                meta, status = item.get("metadata") or {}, item.get("status") or {}
                workloads.append({
                    "apiVersion": item.get("apiVersion"), "kind": item.get("kind"), "namespace": meta.get("namespace"), "name": meta.get("name"),
                    "generation": meta.get("generation"), "desired": status.get("replicas", status.get("desiredNumberScheduled", 0)),
                    "ready": status.get("readyReplicas", status.get("numberReady", status.get("succeeded", 0))),
                    "conditions": [{key: condition.get(key) for key in ("type", "status", "reason", "lastTransitionTime")} for condition in status.get("conditions") or []],
                })
            return {"operation_id": result.get("operation_id"), "count": len(workloads), "workloads": workloads}
        if name == "kubernetes_recent_events":
            namespace = args.get("namespace")
            command = ["get", "events", "--field-selector", "type=Warning", "--sort-by=.lastTimestamp"]
            command += ["--namespace", namespace] if namespace else ["--all-namespaces"]
            result = kubectl_json(command)
            events = []
            for item in result.get("items", [])[-200:]:
                meta, involved = item.get("metadata") or {}, item.get("involvedObject") or {}
                events.append({"namespace": meta.get("namespace"), "reason": item.get("reason"), "message": item.get("message"), "count": item.get("count"), "lastTimestamp": item.get("lastTimestamp"), "object": {key: involved.get(key) for key in ("kind", "name", "namespace")}})
            return {"operation_id": result.get("operation_id"), "count": len(events), "events": events}
        if name in {"harbor_users", "harbor_projects", "harbor_robots", "harbor_repositories", "harbor_artifacts"}:
            client = harbor_admin_client()
            if name == "harbor_users":
                return {"users": client.users(), "credential_material_returned": False}
            if name == "harbor_projects":
                return {"projects": client.projects(), "credential_material_returned": False}
            if name == "harbor_robots":
                return {"robots": client.robots(), "credential_material_returned": False}
            if name == "harbor_repositories":
                return {"project": args["project"], "repositories": client.repositories(args["project"])}
            return {"project": args["project"], "repository": args["repository"], "artifacts": client.artifacts(args["project"], args["repository"])}
        if name == "harbor_tenant_reconcile":
            catalog = tenant_catalog()
            if args["tenant"] not in catalog:
                raise ValueError("tenant is not declared in the Harbor catalog")
            desired = desired_tenant(args["tenant"], catalog[args["tenant"]])
            client = harbor_admin_client()
            user = client.find_user(desired["username"])
            project = client.find_project(desired["project"])
            before = {"user": bool(user), "project": bool(project), "membership": "unknown"}
            after = {**desired, "project_private": True, "credential": "managed" if not user else "external/unmanaged"}
            result = mutation_preview(f"harbor/tenant/{args['tenant']}", before, after, bool(args.get("apply")), before)
            if not args.get("apply"):
                return result
            password = None
            if not project:
                client.create_project(desired["project"])
            if not user:
                password = secrets.token_urlsafe(32)
                client.create_user(desired["username"], desired["email"], password)
            changed_member = client.ensure_project_admin(desired["project"], desired["username"])
            result.update({"applied": True, "project_created": not bool(project), "user_created": not bool(user), "membership_changed": changed_member, "credential_material_returned": False})
            if password:
                reference = credential_store().put(args["tenant"], desired["project"], "human", {"username": desired["username"], "password": password, "project": desired["project"]})
                result["credential"] = redemption_registry().issue(reference, args["tenant"])
            else:
                result["credential_status"] = "external/unmanaged; use the HTTPS adoption flow"
            return result
        if name == "harbor_user_update":
            if "@" not in args["email"] or len(args["email"]) > 254 or not args["realname"].strip():
                raise ValueError("invalid Harbor user profile")
            result = mutation_preview(f"harbor/user/{args['user_id']}", None, {"email": args["email"], "realname": args["realname"]}, bool(args.get("apply")), "restore previous profile from Harbor audit state")
            if args.get("apply"):
                harbor_admin_client().update_user(args["user_id"], args["email"], args["realname"])
            return result
        if name in {"harbor_tenant_password_rotate", "harbor_tenant_adoption_create", "harbor_tenant_suspend"}:
            catalog = tenant_catalog()
            if args["tenant"] not in catalog:
                raise ValueError("tenant is not declared in the Harbor catalog")
            desired = desired_tenant(args["tenant"], catalog[args["tenant"]])
            client = harbor_admin_client()
            user = client.find_user(desired["username"])
            if not user:
                raise ValueError("tenant Harbor user does not exist")
            if name == "harbor_tenant_adoption_create":
                return {"tenant": args["tenant"], "username": desired["username"], **redemption_registry().issue_adoption(args["tenant"]), "credential_material_returned": False}
            if name == "harbor_tenant_password_rotate":
                result = mutation_preview(f"harbor/user/{desired['username']}/password", "managed-or-external", "rotated-to-broker", bool(args.get("apply")), "rotate again")
                if args.get("apply"):
                    password = secrets.token_urlsafe(32)
                    client.reset_user_password(int(user["user_id"]), password)
                    reference = credential_store().put(args["tenant"], desired["project"], "human", {"username": desired["username"], "password": password, "project": desired["project"]})
                    result.update({"applied": True, "credential": redemption_registry().issue(reference, args["tenant"]), "credential_material_returned": False})
                return result
            members = [member for member in client.project_members(desired["project"]) if (member.get("entity") or {}).get("entity_name") == desired["username"]]
            robots = [robot for robot in client.robots() if any(permission.get("namespace") == desired["project"] for permission in robot.get("permissions") or [])]
            result = mutation_preview(f"harbor/tenant/{args['tenant']}/suspension", {"memberships": len(members), "robots": len(robots)}, {"memberships": 0, "robots_disabled": len(robots), "images_deleted": False}, bool(args.get("apply")), "reconcile tenant membership and explicitly re-enable robots")
            if args.get("apply"):
                for member in members:
                    client.remove_project_member(desired["project"], int(member["id"]))
                for robot in robots:
                    client.disable_robot(int(robot["id"]), True)
            return result
        if name in {"harbor_project_member_remove", "harbor_robot_disable", "harbor_artifact_scan", "harbor_artifact_delete"}:
            client = harbor_admin_client()
            apply = bool(args.get("apply"))
            target = {key: value for key, value in args.items() if key != "apply"}
            result = mutation_preview(f"harbor/{name.removeprefix('harbor_')}", None, target, apply, "manual restore from Harbor audit state")
            if apply:
                if name == "harbor_project_member_remove":
                    client.remove_project_member(args["project"], args["member_id"])
                elif name == "harbor_robot_disable":
                    client.disable_robot(args["robot_id"], args["disabled"])
                elif name == "harbor_artifact_scan":
                    client.scan_artifact(args["project"], args["repository"], args["reference"])
                else:
                    client.delete_artifact(args["project"], args["repository"], args["reference"])
            return result
        if name == "mihomos_node_configure":
            node, op = kubectl_object(["get", "node", args["node"]])
            labels = dict((node.get("metadata") or {}).get("labels") or {})
            before = {k: labels.get(k) for k in ("networking.re8ch.com/geo", "networking.re8ch.com/mihomo-exit", "networking.re8ch.com/mihomo-exit-cn")}
            desired = {"networking.re8ch.com/geo": args["geo"]}
            desired["networking.re8ch.com/mihomo-exit"] = "enabled" if args["exit"] == "global" else None
            desired["networking.re8ch.com/mihomo-exit-cn"] = "enabled" if args["exit"] == "cn" else None
            response = mutation_preview(f"node/{args['node']}", before, desired, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["patch", "node", args["node"]], {"metadata": {"labels": desired}})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "mihomos_rule_configure":
            suffix = valid_dns_label(args["domain_suffix"])
            cm, op = kubectl_object(["--namespace", "egress-fabric", "get", "configmap", "mihomos-node-configs"])
            key = args["profile"] + ".yaml"
            before = (cm.get("data") or {}).get(key, "")
            rule = f"  - DOMAIN-SUFFIX,{suffix},{args['action']}"
            lines = [line for line in before.splitlines() if line.strip() != rule.strip()]
            if args.get("present", True):
                index = next((i for i, line in enumerate(lines) if line.strip().startswith("- MATCH,")), len(lines))
                lines.insert(index, rule)
            after = "\n".join(lines) + ("\n" if before.endswith("\n") else "")
            response = mutation_preview(f"configmap/egress-fabric/mihomos-node-configs:{key}", before, after, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["--namespace", "egress-fabric", "patch", "configmap", "mihomos-node-configs"], {"data": {key: after}})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "advanced_fabric_node_configure":
            fabric, op = kubectl_object(["get", "advancedfabric.networking.re8ch.com", "re8ch"])
            nodes = list((fabric.get("spec") or {}).get("nodes") or [])
            target = next((item for item in nodes if item.get("name") == args["node"]), None)
            if target is None:
                raise ValueError("unknown AdvancedFabric node")
            before = dict(target)
            if "uplink_mbps" in args:
                target["uplinkMbps"] = max(1, min(100000, int(args["uplink_mbps"])))
            if "monthly_traffic_limit_gib" in args:
                target["monthlyTrafficLimitGiB"] = max(1, min(10000000, int(args["monthly_traffic_limit_gib"])))
            response = mutation_preview("advancedfabric/re8ch", before, target, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["patch", "advancedfabric.networking.re8ch.com", "re8ch"], {"spec": {"nodes": nodes}})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "public_edge_candidate_configure":
            edge, op = kubectl_object(["get", "publicedge.networking.re8ch.com", args["candidate"]])
            before = dict(edge.get("spec") or {})
            desired = dict(before)
            mappings = {"capacity_mbps": "capacityMbps", "priority": "priority", "enabled": "enabled", "draining": "draining", "service_classes": "serviceClasses"}
            for source, destination in mappings.items():
                if source in args:
                    desired[destination] = args[source]
            response = mutation_preview(f"publicedge/{args['candidate']}", before, desired, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["patch", "publicedge.networking.re8ch.com", args["candidate"]], {"spec": desired})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "public_edge_service_configure":
            hostname = valid_dns_label(args["hostname"]) + "."
            service = valid_dns_label(args["service"])
            probe_path = args.get("probe_path", "/")
            if not probe_path.startswith("/") or ".." in probe_path:
                raise ValueError("probe_path must be an absolute safe path")
            ingress, _ = kubectl_object(["--namespace", args["publication_namespace"], "get", "ingress", args["publication_name"]])
            if (ingress.get("spec") or {}).get("ingressClassName") != "external-dns-only":
                raise ValueError("publication ingress must use ingressClassName external-dns-only")
            cm, op = kubectl_object(["--namespace", "regional-routing", "get", "configmap", "public-edge-manager"])
            services = json.loads((cm.get("data") or {}).get("SERVICES_JSON", "{}"))
            refs = json.loads((cm.get("data") or {}).get("PUBLICATION_REFS_JSON", "{}"))
            before = {"service": services.get(hostname), "publication": refs.get(service)}
            if args.get("present", True):
                services[hostname] = {"service": service, "class": args["service_class"], "probePath": probe_path}
                refs[service] = {"namespace": args["publication_namespace"], "name": args["publication_name"]}
            else:
                services.pop(hostname, None); refs.pop(service, None)
            after = {"service": services.get(hostname), "publication": refs.get(service)}
            response = mutation_preview("configmap/regional-routing/public-edge-manager", before, after, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["--namespace", "regional-routing", "patch", "configmap", "public-edge-manager"], {"data": {"SERVICES_JSON": json.dumps(services, separators=(",", ":")), "PUBLICATION_REFS_JSON": json.dumps(refs, separators=(",", ":"))}})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "public_edge_candidate_policy_configure":
            cm, op = kubectl_object(["--namespace", "regional-routing", "get", "configmap", "public-edge-manager"])
            mapping = {"capacity_weight": "CANDIDATE_CAPACITY_WEIGHT", "local_area_bonus": "CANDIDATE_LOCAL_AREA_BONUS", "priority_weight": "CANDIDATE_PRIORITY_WEIGHT", "latency_divisor_ms": "CANDIDATE_LATENCY_DIVISOR_MS", "latency_penalty_cap": "CANDIDATE_LATENCY_PENALTY_CAP", "local_node_bonus": "CANDIDATE_LOCAL_NODE_BONUS"}
            defaults = {"CANDIDATE_CAPACITY_WEIGHT": "10", "CANDIDATE_LOCAL_AREA_BONUS": "100000", "CANDIDATE_PRIORITY_WEIGHT": "1", "CANDIDATE_LATENCY_DIVISOR_MS": "20", "CANDIDATE_LATENCY_PENALTY_CAP": "50", "CANDIDATE_LOCAL_NODE_BONUS": "5"}
            before = {key: (cm.get("data") or {}).get(key, default) for key, default in defaults.items()}
            after = dict(before)
            for source, destination in mapping.items():
                if source in args:
                    after[destination] = str(args[source])
            response = mutation_preview("configmap/regional-routing/public-edge-manager", before, after, bool(args.get("apply")), before)
            response["operation_id"] = op
            if args.get("apply"):
                changed = kubectl_patch(["--namespace", "regional-routing", "patch", "configmap", "public-edge-manager"], {"data": after})
                response["operation_id"] = changed["operation_id"]
            return response
        if name in {"cilium_helm_app_configure", "rook_csi_app_configure"}:
            if name.startswith("cilium"):
                namespace, configmap = "kube-system", "cilium-config"
                value = "true" if args["value"] else "false"
            else:
                namespace, configmap = "rook-ceph", "rook-ceph-operator-config"
                value = args["value"]
                if args["key"] == "ROOK_LOG_LEVEL" and value not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                    raise ValueError("invalid Rook log level")
                if args["key"] == "ROOK_ENABLE_DISCOVERY_DAEMON" and value not in {"true", "false"}:
                    raise ValueError("Rook discovery value must be true or false")
                if args["key"] == "ROOK_CEPH_COMMANDS_TIMEOUT_SECONDS" and not (value.isdigit() and 5 <= int(value) <= 300):
                    raise ValueError("Rook command timeout must be 5..300 seconds")
            cm, op = kubectl_object(["--namespace", namespace, "get", "configmap", configmap])
            before = (cm.get("data") or {}).get(args["key"])
            response = mutation_preview(f"configmap/{namespace}/{configmap}:{args['key']}", before, value, bool(args.get("apply")), before)
            response["operation_id"] = op
            response["activation"] = "configuration is persisted; workload rollout remains a separate deliberate operation"
            if args.get("apply"):
                changed = kubectl_patch(["--namespace", namespace, "patch", "configmap", configmap], {"data": {args["key"]: value}})
                response["operation_id"] = changed["operation_id"]
            return response
        if name == "mihomos_cluster_status":
            return configmap_json_status("egress-fabric", "app.kubernetes.io/component=mihomos-node-status")
        if name == "advanced_fabric_status":
            return configmap_json_status("kube-system", "networking.re8ch.com/node-status=true")
        if name == "public_edge_status":
            edges = kubectl_json(["get", "publicedges.networking.re8ch.com"])
            routes = kubectl_json(["get", "httproutes.gateway.networking.k8s.io", "--all-namespaces"])
            return {
                "candidates": [{"name": x.get("metadata", {}).get("name"), "spec": x.get("spec"), "conditions": (x.get("status") or {}).get("conditions", [])} for x in edges.get("items", [])],
                "routes": [{"namespace": x.get("metadata", {}).get("namespace"), "name": x.get("metadata", {}).get("name"), "hostnames": (x.get("spec") or {}).get("hostnames", []), "parents": (x.get("status") or {}).get("parents", [])} for x in routes.get("items", []) if (x.get("spec") or {}).get("hostnames")],
                "policy": "read-only; PublicEdge controllers remain DNS and routing authority",
            }
        if name == "cilium_helm_app_status":
            return helm_app_status("kube-system", "cilium", ["k8s-app=cilium", "name=cilium-operator"])
        if name == "rook_csi_app_status":
            return helm_app_status("rook-ceph", "rook-ceph", ["app=rook-ceph-operator", "app=csi-rbdplugin", "app=csi-cephfsplugin"])
        if name == "litellm_gateway_status":
            return {
                "service": "litellm",
                "liveness": litellm_get("/health/liveliness", authenticated=False),
                "readiness": litellm_get("/health/readiness", authenticated=False),
            }
        if name == "litellm_models":
            return {"service": "litellm", **litellm_get("/v1/models")}
        if name == "litellm_spend":
            return {"service": "litellm", **litellm_get("/global/spend")}
        domain = args.get("domain")
        exec_node = resolve_node(os.getenv("INFRA_EXEC_NODE", "restricted-node-6dc3f140f9"))
        routes = {
            "cilium": ("direct", "kubernetes-api"),
            "ceph": ("direct", "kubernetes-api/rook-toolbox"),
            "pigsty": ("indirect", "ops-capsule/ssh"),
            "harbor": ("direct", "service-api-proxy"),
            "grafana": ("direct", "service-api-proxy"),
            "github": ("direct", "github-native-connection"),
            "litellm": ("direct", "cluster-service-api"),
        }
        commands = {
            "cilium": ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), "--namespace", "kube-system", "get", "pods", "-l", "k8s-app=cilium", "-o", "wide"],
            "ceph": ["kubectl", "--kubeconfig", str(KUBECONFIG_DIR / "re8ch.yaml"), "--namespace", "rook-ceph", "exec", "deploy/rook-ceph-tools", "--", "ceph", "status", "--format", "json"],
            "pigsty": ["pg_isready"],
            "harbor": ["curl", "-fsS", os.getenv("HARBOR_HEALTH_URL", "https://registry.re8ch.com/api/v2.0/health")],
            "grafana": ["curl", "-fsS", os.getenv("GRAFANA_HEALTH_URL", "https://grafana.re8ch.com/api/health")],
            "github": ["gh", "repo", "view", os.getenv("GITHUB_REPOSITORY", "re8ch/cloud-functions"), "--json", "nameWithOwner,defaultBranchRef"],
            "litellm": ["curl", "-fsS", os.getenv("LITELLM_BASE_URL", "http://litellm.llm-gateway.svc.cluster.local:4000") + "/health/readiness"],
        }
        if name == "infra_observe":
            interface_mode, transport = routes[domain]
            if domain in {"cilium", "ceph"}:
                result = command_result(commands[domain], None, 120)
            elif domain in {"harbor", "grafana", "litellm"}:
                result = command_result(commands[domain], None, 120)
            elif domain == "github":
                token = os.getenv("GH_TOKEN")
                if not token:
                    raise RuntimeError("credential reference is unavailable: github-native")
                remote = "GH_TOKEN=$(cat) " + shlex.join(commands[domain])
                result = command_result(ssh_argv(exec_node, remote), token, 120)
            else:
                result = command_result(ssh_argv(exec_node, shlex.join(commands[domain])), None, 120)
            return {"domain": domain, "interface_mode": interface_mode, "transport": transport, **result}
        if name == "infra_execute":
            if domain == "harbor":
                raise ValueError("harborctl compatibility transport is retired; use native harbor_* tools")
            prefix = {"cilium": ["cilium"], "ceph": ["ceph"], "pigsty": ["patronictl"], "harbor": ["harborctl"], "grafana": ["grafana-cli"], "github": ["gh"]}[domain]
            argv = args["argv"]
            if not argv or argv[0] != prefix[0]:
                raise ValueError(f"{domain} command must start with {prefix[0]}")
            if domain == "github":
                token = os.getenv("GH_TOKEN")
                if not token:
                    raise RuntimeError("credential reference is unavailable: github-native")
                remote = "GH_TOKEN=$(cat) " + shlex.join(argv)
                return command_result(ssh_argv(exec_node, remote), token, int(args.get("timeout_seconds", 600)))
            return command_result(ssh_argv(exec_node, shlex.join(argv)), args.get("stdin"), int(args.get("timeout_seconds", 600)))

    if PROFILE in {"tenant", "all"} and name in {tool["name"] for tool in TENANT_TOOLS}:
        if name == "harbor_tenant_browser_launch":
            credential_store().get(args["credential_ref"], args["tenant"])
            return {**redemption_registry().issue(args["credential_ref"], args["tenant"]), "purpose": "controlled-browser-login", "credential_material_returned": False}
        if name == "harbor_tenant_kubernetes_inject":
            preview = mutation_preview(f"secret/{args['tenant']}/{args['secret_name']}", None, {"type": "kubernetes.io/dockerconfigjson", "credential_material_returned": False}, bool(args.get("apply")), "delete generated Secret")
            if args.get("apply"):
                preview.update(credential_store().inject_dockerconfig(args["credential_ref"], args["tenant"], args["tenant"], args["secret_name"], urllib.parse.urlparse(HARBOR_URL).netloc))
            return preview
        client, project = tenant_client(args["tenant"], args["credential_ref"])
        if name == "harbor_tenant_repositories":
            return {"tenant": args["tenant"], "project": project, "repositories": client.repositories(project)}
        if name == "harbor_tenant_artifacts":
            return {"tenant": args["tenant"], "project": project, "repository": args["repository"], "artifacts": client.artifacts(project, args["repository"])}
        if name == "harbor_tenant_robot_create":
            preview = mutation_preview(f"harbor/{project}/robot/{args['name']}", None, {"permission": args["permission"], "duration_days": args.get("duration_days", 90)}, bool(args.get("apply")), "disable generated robot")
            if not args.get("apply"):
                return preview
            robot, token = client.create_robot(project, args["name"], args["permission"], int(args.get("duration_days", 90)))
            username = str(robot.get("name") or args["name"])
            reference = credential_store().put(args["tenant"], project, "robot", {"username": username, "password": token, "project": project})
            preview.update({"applied": True, "robot": robot, "credential": redemption_registry().issue(reference, args["tenant"]), "credential_material_returned": False})
            return preview
        preview = mutation_preview(f"harbor/{project}/robot/{args['robot_id']}", {"disabled": False}, {"disabled": True}, bool(args.get("apply")), {"disabled": False})
        if args.get("apply"):
            client.disable_robot(args["robot_id"], True)
        return preview

    tenant_profiles = {"tenant-infra", "tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc", "all"}
    tenant_tool_names = {tool["name"] for tool in TENANT_INFRA_TOOLS + TENANT_BYOC_TOOLS}
    if PROFILE in tenant_profiles and name in tenant_tool_names:
        tenant = authenticated_tenant(identity)
        store = tenant_service_store()
        admin_tools = {"tenant_create", "tenant_list", "tenant_grant_create", "tenant_visibility_update", "tenant_delete"}
        if name in admin_tools | {"byoc_connection_approve"} and (PROFILE != "tenant-admin" or tenant != "admin"):
            raise PermissionError("the protected admin tenant is required")
        if PROFILE == "tenant-registry" and name in {"service_instance_plan", "service_instance_create"} and args.get("spec", {}).get("serviceClass") != "registry.harbor.v1":
            raise PermissionError("registry plugin accepts only registry.harbor.v1")
        if PROFILE == "tenant-observability" and name in {"service_instance_plan", "service_instance_create"} and args.get("spec", {}).get("serviceClass") != "observability.grafana.v1":
            raise PermissionError("observability plugin accepts only observability.grafana.v1")
        if name == "tenant_create":
            return store.create_tenant(args["tenant_ref"], args["display_name"], args["identity_ref"])
        if name == "tenant_list":
            return {"tenantRef": tenant, "tenants": store.tenants()}
        if name == "tenant_grant_create":
            return store.create_grant(args["tenant_ref"], args["consumable_ref"], args["role_ref"], args["visibility"], args.get("constraints") or {})
        if name == "tenant_visibility_update":
            return store.set_visibility(args["tenant_ref"], args["visibility"])
        if name == "tenant_delete":
            return store.delete_tenant(args["tenant_ref"])
        if name == "byoc_cluster_register":
            return store.register_cluster(tenant, args["cluster_uid"], args["region_ref"], args["issuer"], args["public_key"], args.get("capabilities") or [])
        if name == "byoc_registration_list":
            target = args.get("tenant_ref") if tenant == "admin" else tenant
            return {"tenantRef": target, "registrations": store.registrations(target)}
        if name == "byoc_connection_list":
            return {"tenantRef": tenant, "connections": store.connections(tenant)}
        if name == "byoc_connection_approve":
            return store.approve_cluster(args["registration_ref"])
        if name == "byoc_connection_revoke":
            target = args.get("tenant_ref") if tenant == "admin" else tenant
            if target != tenant and tenant != "admin":
                raise PermissionError("cross-tenant connection revoke is forbidden")
            return store.revoke_connection(target, args["connection_ref"], bool(args.get("offline")))
        if name == "service_catalog_list":
            return {"tenantRef": tenant, "services": tenant_catalog_for_profile(), "pricingIncluded": False}
        if name == "service_instance_plan":
            return store.plan(tenant, args["name"], args["spec"])
        if name == "service_instance_create":
            return store.mutate(tenant, args["name"], args["spec"], args["idempotency_key"], "create")
        if name == "service_instance_get":
            return store.get(tenant, args["resource_ref"])
        if name == "service_instance_list":
            return {"tenantRef": tenant, "instances": store.list(tenant)}
        if name in {"service_instance_update", "service_instance_suspend", "service_instance_resume", "service_instance_delete"}:
            current = store.get(tenant, args["resource_ref"])
            spec = dict(args.get("spec") or current["spec"])
            if args.get("spec") and spec.get("serviceClass") != current["service_class"]:
                raise ServiceError("serviceClass is immutable")
            desired = {"service_instance_suspend": "Suspended", "service_instance_resume": "Ready", "service_instance_delete": "Deleted"}.get(name)
            if desired:
                spec["desiredState"] = desired
            return store.mutate(tenant, current["name"], spec, args["idempotency_key"], name.removeprefix("service_instance_"))
        if name == "service_binding_create":
            return store.create_binding(tenant, args["resource_ref"], args["binding_type"], args.get("parameters") or {})
        if name == "service_binding_list":
            return {"tenantRef": tenant, "bindings": store.bindings(tenant, args.get("resource_ref"))}
        if name == "service_binding_revoke":
            return store.revoke_binding(tenant, args["binding_ref"])
        if name == "usage_summary_get":
            return store.usage_summary(tenant, args.get("resource_ref"))
        if name == "operation_get":
            return store.operation(tenant, args["operation_id"])

    if PROFILE in {"orchestra", "all"}:
        if name == "link_execution":
            return {k: args.get(k) for k in ("task_id", "kagent_session_id", "trace_id", "codex_thread_id", "artifact_id")}
        service = args.get("service")
        base = os.getenv(f"{service.upper()}_BASE_URL", "").rstrip("/")
        if not base:
            raise ValueError("service base URL is not configured")
        path = ORCHESTRA_HEALTH_PATHS.get(service, "/healthz") if name == "orchestra_service_status" else args["path"]
        if not path.startswith("/") or ".." in path:
            raise ValueError("invalid service path")
        method = "GET" if name == "orchestra_service_status" else args["method"]
        body = None if name == "orchestra_service_status" else json.dumps(args.get("body")).encode()
        headers = {"Accept": "application/json"}
        token = os.getenv(f"{service.upper()}_API_TOKEN")
        if token:
            if service == "opensandbox":
                headers["OPEN-SANDBOX-API-KEY"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(MAX_OUTPUT)
                return {"service": service, "status": response.status, "body": raw.decode(errors="replace")}
        except urllib.error.HTTPError as exc:
            return {"service": service, "status": exc.code, "body": exc.read(MAX_OUTPUT).decode(errors="replace")}
    raise ValueError("tool is not available in this profile")


class Handler(BaseHTTPRequestHandler):
    server_version = "re8ch-ai-operations/0.1"

    def send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers(); self.wfile.write(raw)

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_html(self, status: int, body: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def marketplace_login(self, query: dict[str, list[str]]) -> None:
        if PROFILE != "orchestra" or not INSTALL_STATE_HMAC_KEY:
            self.send_json(404, {"error": "not_found"}); return
        plugin = (query.get("plugin") or [""])[0]
        plugins = {"re8ch-cloud-platform-tool", "re8ch-cluster-infra-tool", "re8ch-agent-orchestra-collab"}
        if plugin not in plugins or (query.get("channel") or [""])[0] != "codex":
            self.send_html(400, "<h1>请选择有效的 RE8CH 模块和 Codex 渠道</h1>"); return
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        with INSTALL_STATE_LOCK:
            INSTALL_STATES[nonce] = (verifier, now(), plugin)
            for key, (_, created, _) in list(INSTALL_STATES.items()):
                if now() - created > 600:
                    INSTALL_STATES.pop(key, None)
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        issued_at = now()
        query = urllib.parse.urlencode({
            "client_id": INSTALL_CLIENT_ID,
            "redirect_uri": INSTALL_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email groups",
            "state": signed_install_state(nonce, issued_at),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        self.redirect(f"{DEX_AUTH_URL}?{query}")

    def marketplace_callback(self, query: dict[str, list[str]]) -> None:
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        nonce = verify_install_state(state)
        with INSTALL_STATE_LOCK:
            saved = INSTALL_STATES.pop(nonce, None) if nonce else None
        if not saved or not code:
            self.send_html(400, "<h1>安装会话无效或已过期</h1>"); return
        verifier, _, plugin = saved
        encoded = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": INSTALL_CLIENT_ID,
            "redirect_uri": INSTALL_REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }).encode()
        try:
            request = urllib.request.Request(DEX_TOKEN_URL, data=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(request, timeout=15) as response:
                token = json.load(response)["access_token"]
            identity = dex_identity(token)
        except Exception:
            identity = None
        if not identity:
            self.send_html(403, "<h1>Dex 授权失败</h1>"); return
        install_link = f"codex://plugins/{plugin}"
        marketplace_name = "re8ch-admin-private"
        add_command = f"codex plugin marketplace add {MARKETPLACE_SOURCE}"
        install_command = f"codex plugin add {plugin}@{marketplace_name}"
        upgrade_command = f"codex plugin marketplace upgrade {marketplace_name}"
        commands = "\n".join((add_command, install_command))
        page = f'''<!doctype html><meta charset="utf-8"><title>RE8CH AI Tools</title>
<style>body{{font:16px system-ui;max-width:760px;margin:64px auto;padding:0 24px}}a,button{{display:block;box-sizing:border-box;width:100%;padding:14px;margin:10px 0;border:1px solid #777;border-radius:10px;text-decoration:none;background:transparent;color:inherit;text-align:left;font:inherit;cursor:pointer}}pre{{padding:14px;border-radius:10px;background:#eee;white-space:pre-wrap;word-break:break-all}}.muted{{color:#666}}</style>
<h1>Dex 登录成功</h1><p>账号：{html.escape(str(identity.get("email") or identity.get("sub") or "authorized"))}</p>
<p><strong>Codex 已授权，但插件尚未安装。</strong>Codex URL 只能打开已经注册的 Marketplace 插件页，不能注册远程 Marketplace。</p>
<p>请在终端依次运行以下两条 Codex 标准命令：</p>
<pre id="commands">{html.escape(commands)}</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('commands').innerText).then(() => this.innerText='已复制安装命令')">复制安装命令</button>
<p>命令成功后再打开插件详情；此按钮本身不会安装插件：</p>
<p><a href="{html.escape(install_link)}">在 Codex 中打开 {html.escape(plugin)}</a></p>
<p>后续更新：<code>{html.escape(upgrade_command)}</code></p>
<p class="muted">Plugin Management 用于管理已安装/已发现插件，不替代私有 Git Marketplace 注册。</p>
<p>Marketplace source：<code>{html.escape(MARKETPLACE_SOURCE)}</code></p>'''
        self.send_html(200, page)

    def identity(self) -> dict[str, Any] | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:]
        if SERVICE_API_TOKEN and hmac.compare_digest(token, SERVICE_API_TOKEN):
            return {"subject": "service:litellm", "groups": sorted(PROFILE_GROUPS.get(PROFILE, set())), "auth_type": "service"}
        return registry().authenticate(token) if token.startswith("r8d_") else dex_identity(token)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/credentials/redeem":
            self.send_html(200, '''<!doctype html><meta charset="utf-8"><title>RE8CH Credential Delivery</title>
<style>body{font:16px system-ui;max-width:680px;margin:64px auto;padding:0 24px}button,pre{box-sizing:border-box;width:100%;padding:14px;border:1px solid #777;border-radius:10px}pre{white-space:pre-wrap;word-break:break-all;background:#eee}</style>
<h1>一次性凭据交付</h1><p>凭据只显示一次，页面不会缓存。请确认当前设备可信。</p><button id="redeem">显示凭据</button><pre id="output">尚未兑换</pre>
<script>document.getElementById('redeem').onclick=async()=>{const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const r=await fetch('/credentials/redeem',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token})});document.getElementById('output').textContent=r.ok?JSON.stringify(await r.json(),null,2):'凭据无效、已使用或已过期';document.getElementById('redeem').disabled=true}</script>'''); return
        if parsed.path == "/credentials/adopt":
            self.send_html(200, '''<!doctype html><meta charset="utf-8"><title>Adopt Harbor Credential</title>
<style>body{font:16px system-ui;max-width:680px;margin:64px auto;padding:0 24px}input,button,pre{box-sizing:border-box;width:100%;padding:12px;margin:6px 0}pre{white-space:pre-wrap;background:#eee}</style><h1>导入现有 Harbor 登录</h1><p>密码通过本次 HTTPS 请求直接进入 Credential Broker，不进入 MCP 或 AI 上下文。</p><input id="username" autocomplete="username" placeholder="Harbor username"><input id="password" type="password" autocomplete="current-password" placeholder="Harbor password"><button id="adopt">验证并导入</button><pre id="output"></pre><script>document.getElementById('adopt').onclick=async()=>{const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const username=document.getElementById('username').value,password=document.getElementById('password').value;document.getElementById('password').value='';const r=await fetch('/credentials/adopt',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,username,password})});document.getElementById('output').textContent=JSON.stringify(await r.json(),null,2);document.getElementById('adopt').disabled=true}</script>'''); return
        if parsed.path == "/install":
            self.redirect("https://tools.re8ch.com/catalog"); return
        if parsed.path == "/install/authorize":
            self.marketplace_login(urllib.parse.parse_qs(parsed.query)); return
        if parsed.path == "/install/callback":
            self.marketplace_callback(urllib.parse.parse_qs(parsed.query)); return
        if self.path.endswith("/healthz"):
            self.send_json(200, {"status": "ok", "profile": PROFILE, "audience": AUDIENCE}); return
        if "/.well-known/oauth-protected-resource" in self.path:
            origin = os.getenv("PUBLIC_MCP_URL", f"https://{PROFILE}.tools.re8ch.com/mcp")
            self.send_json(200, {"resource": origin, "authorization_servers": ["https://dex.re8ch.com"], "bearer_methods_supported": ["header"], "scopes_supported": ["openid", "profile", "email", "groups", "offline_access"]}); return
        if parsed.path.endswith("/mcp"):
            self.send_json(405, {"error": "method_not_allowed", "allowed": ["POST"]}, {"Allow": "POST"}); return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path == "/credentials/redeem":
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 8192)
                payload = json.loads(self.rfile.read(length))
                consumed = redemption_registry().consume(str(payload.get("token") or ""))
                if not consumed:
                    self.send_json(410, {"error": "expired_or_consumed"}, {"Cache-Control": "no-store"}); return
                reference, tenant = consumed
                material = credential_store().get(reference, tenant)
                self.send_json(200, {"tenant": tenant, "registry": HARBOR_URL, **material}, {"Cache-Control": "no-store", "Pragma": "no-cache"}); return
            except Exception:
                self.send_json(400, {"error": "invalid_redemption"}, {"Cache-Control": "no-store"}); return
        if urllib.parse.urlparse(self.path).path == "/credentials/adopt":
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 16384)
                payload = json.loads(self.rfile.read(length))
                tenant = redemption_registry().consume_adoption(str(payload.get("token") or ""))
                declared = tenant_catalog().get(tenant or "")
                if not tenant or declared is None:
                    self.send_json(410, {"error": "expired_or_consumed"}, {"Cache-Control": "no-store"}); return
                desired = desired_tenant(tenant, declared)
                username, password = str(payload.get("username") or ""), str(payload.get("password") or "")
                if username != desired["username"] or not password:
                    raise PermissionError("credential does not match the declared tenant account")
                client = HarborClient(HARBOR_URL, username, password)
                if desired["project"] not in {project.get("name") for project in client.projects()}:
                    raise PermissionError("credential cannot access the declared tenant project")
                reference = credential_store().put(tenant, desired["project"], "human", {"username": username, "password": password, "project": desired["project"]})
                self.send_json(200, {"tenant": tenant, "credential_ref": reference, "credential_material_returned": False}, {"Cache-Control": "no-store"}); return
            except Exception:
                self.send_json(400, {"error": "adoption_failed"}, {"Cache-Control": "no-store"}); return
        if not self.path.endswith("/mcp"):
            self.send_json(404, {"error": "not_found"}); return
        identity = self.identity()
        if not identity:
            metadata = os.getenv(
                "PUBLIC_RESOURCE_METADATA_URL",
                os.getenv("PUBLIC_ORIGIN", f"https://{PROFILE}.tools.re8ch.com") + "/.well-known/oauth-protected-resource/mcp",
            )
            self.send_json(401, {"error": "invalid_token"}, {"WWW-Authenticate": f'Bearer realm="https://dex.re8ch.com", resource_metadata="{metadata}"'}); return
        if not allowed(identity):
            self.send_json(403, {"error": "forbidden"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            method = request.get("method")
            rid = request.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}}, "serverInfo": {"name": f"re8ch-{PROFILE}", "version": "0.2.0"}}
            elif method == "tools/list":
                result = {"tools": tools()}
            elif method == "resources/list":
                result = resources_list(identity, (request.get("params") or {}).get("cursor"))
            elif method == "resources/read":
                result = resources_read(identity, str((request.get("params") or {}).get("uri") or ""))
            elif method == "tools/call":
                params = request.get("params") or {}
                value = call_tool(params.get("name", ""), params.get("arguments") or {}, identity)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
            elif method == "notifications/initialized":
                self.send_response(202); self.end_headers(); return
            else:
                self.send_json(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}); return
            self.send_json(200, {"jsonrpc": "2.0", "id": rid, "result": result})
        except PermissionError as exc:
            self.send_json(403, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32003, "message": str(exc)}})
        except Exception as exc:
            self.send_json(200, {"jsonrpc": "2.0", "id": request.get("id") if "request" in locals() else None, "error": {"code": -32602, "message": str(exc)}})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(json.dumps({"time": now(), "client": self.client_address[0], "message": fmt % args, "profile": PROFILE}), flush=True)


def main() -> None:
    profiles = {"cloud", "infra", "orchestra", "tenant", "tenant-infra", "tenant-admin", "tenant-registry", "tenant-observability", "tenant-byoc", "all"}
    if PROFILE not in profiles:
        raise SystemExit(f"RE8CH_TOOL_PROFILE must be one of: {', '.join(sorted(profiles))}")
    registry()
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
