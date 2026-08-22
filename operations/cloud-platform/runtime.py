"""Typed Cloud Platform operations used by the MCP transport adapter.

The module never loads credential bytes into a result. It passes credential
references to server-side executors and returns an operation/audit envelope.
"""

from __future__ import annotations

import json
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
        target = self.inventory.get(name)
        allowed = set(target["spec"].get("commands", {}).get("read", [])) | set(target["spec"].get("commands", {}).get("write", []))
        if not argv or argv[0] not in allowed:
            raise OperationError("COMMAND_NOT_DECLARED", argv[0] if argv else "empty")
        return self.runner.run(self._ssh_argv(name, argv), target=name, timeout=timeout)

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
        argv = [verb, resource]
        if name:
            argv.append(name)
        if namespace:
            argv += ["--namespace", namespace]
        stdin = None
        if manifest is not None:
            if any(key in manifest for key in SECRET_KEYS):
                raise OperationError("SECRET_VALUE_FORBIDDEN", "use a Credential Broker content reference")
            stdin = json.dumps(manifest)
            if verb == "apply":
                argv += ["--server-side", "--field-manager=re8ch-cloud-platform", "-f", "-"]
            elif verb == "patch":
                argv += ["--type=merge", "-p", stdin]
                stdin = None
        if resource_version:
            argv += [f"--resource-version={resource_version}"]
        if verb in {"get", "list"}:
            argv += ["-o", "json"]
        return self._kubectl(argv, stdin=stdin)

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

    def _kubectl(self, argv: list[str], stdin: str | None = None) -> dict[str, Any]:
        return self.runner.run(["kubectl", "--kubeconfig", self.kubeconfig, *argv], target="re8ch-k3s", stdin=stdin)

    def _ssh_argv(self, target: str, remote_argv: list[str]) -> list[str]:
        return ["ssh", "-F", self.ssh_config, "--", target, "--", *remote_argv]

    @staticmethod
    def _json_stdout(result: dict[str, Any]) -> Any:
        try:
            return redact(json.loads(result["stdout"]))
        except (json.JSONDecodeError, KeyError):
            return None
