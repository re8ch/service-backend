#!/usr/bin/env python3
"""RE8CH three-plane MCP server with Dex activation and permanent device tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
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

PROFILE_GROUPS = {
    "cloud": {ADMIN_GROUP},
    "infra": {ADMIN_GROUP, "re8ch:infra-operators"},
    "orchestra": {ADMIN_GROUP, "re8ch:agent-operators"},
    "all": {ADMIN_GROUP, "re8ch:infra-operators", "re8ch:agent-operators"},
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
    "harbor-api": {"kind": "environment", "env": "HARBOR_API_TOKEN", "actions": ["harbor-api"]},
    "grafana-api": {"kind": "environment", "env": "GRAFANA_API_TOKEN", "actions": ["grafana-api"]},
    "litellm-api": {"kind": "environment", "env": "LITELLM_API_TOKEN", "actions": ["litellm-read"]},
    "github-native": {"kind": "native-connection", "actions": ["github-api"]},
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
    return {"subject": subject, "groups": groups, "auth_type": "dex"}


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
    {"name": "litellm_gateway_status", "description": "Read the cluster LiteLLM liveness and readiness endpoints without exposing credentials.", "inputSchema": schema({})},
    {"name": "litellm_models", "description": "List models exposed by the cluster LiteLLM gateway using its server-held credential.", "inputSchema": schema({})},
    {"name": "litellm_spend", "description": "Read LiteLLM aggregate spend data using its server-held credential.", "inputSchema": schema({})},
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
        return COMMON_TOOLS + CLOUD_TOOLS + INFRA_TOOLS + ORCHESTRA_TOOLS
    return COMMON_TOOLS + {"cloud": CLOUD_TOOLS, "infra": INFRA_TOOLS, "orchestra": ORCHESTRA_TOOLS}.get(PROFILE, [])


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


def ssh_argv(node: dict[str, Any], command: str) -> list[str]:
    return ["ssh", "-F", SSH_CONFIG, "--", node["target"], command]


def call_tool(name: str, args: dict[str, Any], identity: dict[str, Any]) -> Any:
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
            else:
                result = command_result(ssh_argv(exec_node, shlex.join(commands[domain])), None, 120)
            return {"domain": domain, "interface_mode": interface_mode, "transport": transport, **result}
        if name == "infra_execute":
            prefix = {"cilium": ["cilium"], "ceph": ["ceph"], "pigsty": ["patronictl"], "harbor": ["harborctl"], "grafana": ["grafana-cli"], "github": ["gh"]}[domain]
            argv = args["argv"]
            if not argv or argv[0] != prefix[0]:
                raise ValueError(f"{domain} command must start with {prefix[0]}")
            return command_result(ssh_argv(exec_node, shlex.join(argv)), args.get("stdin"), int(args.get("timeout_seconds", 600)))

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
        return registry().authenticate(token) if token.startswith("r8d_") else dex_identity(token)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
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
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": f"re8ch-{PROFILE}", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": tools()}
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
    if PROFILE not in {"cloud", "infra", "orchestra", "all"}:
        raise SystemExit("RE8CH_TOOL_PROFILE must be cloud, infra, orchestra, or all")
    registry()
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
