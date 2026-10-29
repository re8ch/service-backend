"""Typed Cloud Platform operations used by the MCP transport adapter.

The module never loads credential bytes into a result. It passes credential
references to server-side executors and returns an operation/audit envelope.
"""

from __future__ import annotations

import json
import secrets
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

SECRET_KEYS = {"data", "stringData", "token", "password", "privateKey", "clientSecret"}
READ_ONLY_KUBE_VERBS = {"get", "list", "watch", "logs", "events", "auth-can-i"}


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key in SECRET_KEYS else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class Identity:
    subject: str
    groups: tuple[str, ...]


class OperationRunner:
    def __init__(self, executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.executor = executor

    def run(self, argv: list[str], *, target: str, timeout: int = 30, stdin: str | None = None) -> dict[str, Any]:
        operation_id = f"op_{uuid.uuid4().hex}"
        started = time.time()
        try:
            result = self.executor(argv, input=stdin, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise OperationError("BACKEND_TIMEOUT", f"{target} timed out", retryable=True) from exc
        return {
            "operationId": operation_id,
            "target": target,
            "exitCode": result.returncode,
            "stdout": result.stdout[-4_194_304:],
            "stderr": result.stderr[-262_144:],
            "truncated": len(result.stdout) > 4_194_304 or len(result.stderr) > 262_144,
            "durationMs": round((time.time() - started) * 1000),
            "auditRef": f"audit://operations/{operation_id}",
        }


class Inventory:
    def __init__(self, paths: list[Path]):
        items: list[dict[str, Any]] = []
        for path in paths:
            document = yaml.safe_load(path.read_text())
            items.extend(document.get("items", []))
        self.targets = {item["metadata"]["name"]: item for item in items}

    def list(self) -> list[dict[str, Any]]:
        return [redact(self.targets[name]) for name in sorted(self.targets)]

    def get(self, name: str) -> dict[str, Any]:
        if name not in self.targets:
            raise OperationError("TARGET_NOT_FOUND", name)
        return redact(self.targets[name])


class CloudPlatform:
    def __init__(self, inventory: Inventory, runner: OperationRunner, *, kubeconfig: str, ssh_config: str):
        self.inventory = inventory
        self.runner = runner
        self.kubeconfig = kubeconfig
        self.ssh_config = ssh_config

    def nodes_list(self) -> dict[str, Any]:
        desired = [target for target in self.inventory.list() if "k3s-" in " ".join(target["spec"]["capabilities"])]
        live = self._kubectl(["get", "nodes", "-o", "json"])
        return {"desired": desired, "live": self._json_stdout(live), "operation": live}

    def nodes_get(self, name: str) -> dict[str, Any]:
        desired = self.inventory.get(name)
        live = self._kubectl(["get", "node", name, "-o", "json"])
        return {"desired": desired, "live": self._json_stdout(live), "operation": live}

    def node_reachability(self, name: str) -> dict[str, Any]:
        self.inventory.get(name)
        return self.runner.run(self._ssh_argv(name, ["hostname"]), target=name, timeout=15)

    def node_ssh_exec(self, name: str, argv: list[str], timeout: int = 30) -> dict[str, Any]:
        self.inventory.get(name)
        if not argv:
            raise OperationError("INVALID_ARGUMENT", "argv must not be empty")
        return self.runner.run(self._ssh_argv(name, argv), target=name, timeout=timeout)

    def kubectl_exec(self, argv: list[str], *, stdin: str | None = None, timeout: int = 30) -> dict[str, Any]:
        if not argv:
            raise OperationError("INVALID_ARGUMENT", "argv must not be empty")
        return self.runner.run(["kubectl", "--kubeconfig", self.kubeconfig, *argv], target="re8ch-k3s", timeout=timeout, stdin=stdin)

    def kube_simple(self, resource: str, *, namespace: str | None = None, name: str | None = None,
                    labels: str | None = None, fields: str | None = None) -> dict[str, Any]:
        if resource not in {"nodes", "namespaces", "pods", "deployments", "services", "endpoints", "secrets"}:
            raise OperationError("RESOURCE_NOT_ALLOWED", resource)
        argv = ["get", resource]
        if name:
            argv.append(name)
        if namespace:
            argv += ["--namespace", namespace]
        if labels:
            argv += ["--selector", labels]
        if fields:
            argv += ["--field-selector", fields]
        argv += ["-o", "json"]
        result = self._kubectl(argv)
        parsed = self._json_stdout(result)
        if resource == "secrets":
            parsed = redact(parsed)
            for item in ([parsed] if isinstance(parsed, dict) and "items" not in parsed else parsed.get("items", [])):
                if isinstance(item, dict):
                    item.pop("data", None)
                    item.pop("stringData", None)
            # kubectl's raw JSON is part of the operation envelope for normal
            # resources. Secret reads must retain metadata only, even for admins.
            result = {**result, "stdout": "[SECRET_PAYLOAD_WITHHELD]", "stderr": ""}
        return {"result": parsed, "operation": result}

    def kube_resource(self, verb: str, resource: str, *, namespace: str | None = None,
                      name: str | None = None, manifest: dict[str, Any] | None = None,
                      resource_version: str | None = None) -> dict[str, Any]:
        if verb not in {"get", "list", "watch", "apply", "patch", "delete"}:
            raise OperationError("VERB_NOT_ALLOWED", verb)
        # kubectl apply takes a manifest file, not a positional resource type.
        argv = [verb] if verb == "apply" else [verb, resource]
        if name and verb != "apply":
            argv.append(name)
        if namespace:
            argv += ["--namespace", namespace]
        stdin = None
        if resource_version and verb == "patch":
            if manifest is None:
                raise OperationError("MANIFEST_REQUIRED", "patch resourceVersion requires a manifest")
            metadata = dict(manifest.get("metadata") or {})
            if metadata.get("resourceVersion", resource_version) != resource_version:
                raise OperationError("RESOURCE_VERSION_CONFLICT", "manifest and argument resourceVersion differ")
            manifest = {**manifest, "metadata": {**metadata, "resourceVersion": resource_version}}
        if manifest is not None:
            if any(key in manifest for key in SECRET_KEYS):
                raise OperationError("SECRET_VALUE_FORBIDDEN", "use a Credential Broker content reference")
            stdin = json.dumps(manifest)
            if verb == "apply":
                argv += ["--server-side", "--field-manager=re8ch-cloud-platform", "-f", "-"]
            elif verb == "patch":
                argv += ["--type=merge", "-p", stdin]
                stdin = None
        if resource_version and verb != "patch":
            argv += [f"--resource-version={resource_version}"]
        if verb in {"get", "list"}:
            argv += ["-o", "json"]
        return self._kubectl(argv, stdin=stdin)

    def ensure_webmail_session_secret(self, *, apply: bool = False) -> dict[str, Any]:
        """Create the reviewed system credential without accepting or returning values."""
        target = {"namespace": "mail-system", "name": "stalwart-webmail-session",
                  "keys": ["session-secret"], "applied": False}
        if not apply:
            return target
        # The subprocess emits only presence, never credential bytes. Fail closed
        # on errors and preserve existing credentials (including concurrent creates).
        def exists() -> bool:
            result = self._kubectl([
                "get", "secret", target["name"], "-n", target["namespace"],
                "--ignore-not-found", "-o",
                'go-template={{if index .data "session-secret"}}present{{else}}invalid{{end}}',
            ])
            if result["exitCode"]:
                raise OperationError("CREDENTIAL_STATUS_FAILED", "cannot check Webmail credential")
            status = result["stdout"].strip()
            if status not in {"", "present"}:
                raise OperationError("CREDENTIAL_INVALID", "existing Webmail Secret lacks session-secret; refusing rotation")
            return status == "present"

        if exists():
            return {**target, "applied": True, "created": False}
        manifest = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                    "metadata": {"name": target["name"], "namespace": target["namespace"]},
                    "stringData": {"session-secret": secrets.token_hex(32)}}
        result = self._kubectl(["create", "-f", "-", "-o", "name"], stdin=json.dumps(manifest))
        # Never return raw subprocess errors: API validation errors can echo input.
        if result["exitCode"]:
            if exists():
                return {**target, "applied": True, "created": False}
            raise OperationError("CREDENTIAL_CREATE_FAILED", "Webmail credential creation failed")
        if not exists():
            raise OperationError("CREDENTIAL_VERIFY_FAILED", "Webmail credential was not observed after creation")
        return {**target, "applied": True, "created": True,
                "operationId": result["operationId"], "auditRef": result["auditRef"]}

    def kube_events(self, *, namespace: str | None = None, involved_object: str | None = None,
                    reason: str | None = None, event_type: str | None = None, watch: bool = False) -> dict[str, Any]:
        argv = ["get", "events", "--sort-by=.metadata.creationTimestamp", "-o", "json"]
        if namespace:
            argv += ["--namespace", namespace]
        selectors = []
        if involved_object:
            selectors.append(f"involvedObject.name={involved_object}")
        if reason:
            selectors.append(f"reason={reason}")
        if event_type:
            selectors.append(f"type={event_type}")
        if selectors:
            argv += ["--field-selector", ",".join(selectors)]
        if watch:
            argv += ["--watch-only"]
        return self._kubectl(argv)

    def _plugin_http_get(self, path: str) -> dict[str, Any]:
        from plugin_http_transport import get
        return get(self.kubeconfig, path)

    def _plugin_http_request(self, method: str, path: str, body: bytes | None = None, content_type: str = "application/json") -> dict[str, Any]:
        from plugin_http_transport import request
        return request(self.kubeconfig, method, path, body, content_type)

    def _kubectl(self, argv: list[str], stdin: str | None = None) -> dict[str, Any]:
        return self.runner.run(["kubectl", "--kubeconfig", self.kubeconfig, *argv], target="re8ch-k3s", stdin=stdin)

    def _ssh_argv(self, target: str, remote_argv: list[str]) -> list[str]:
        spec = self.inventory.get(target)["spec"]
        address = spec.get("address", target)
        # OpenSSH joins command arguments before the remote shell parses them.
        # POSIX quoting preserves argv, including scripts and literal metacharacters.
        # Windows hosts retain their native command-line parsing.
        command = remote_argv if "windows-host" in spec.get("capabilities", []) else [shlex.join(remote_argv)]
        return ["ssh", "-F", self.ssh_config, "--", address, *command]

    @staticmethod
    def _json_stdout(result: dict[str, Any]) -> Any:
        try:
            return redact(json.loads(result["stdout"]))
        except (json.JSONDecodeError, KeyError):
            return None
