"""Kubernetes-independent audited SSH execution runtime."""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import yaml


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TargetRegistry:
    """Read reviewed SSH targets without consulting the Kubernetes API."""

    def __init__(self, path: Path):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.targets = {
            item["metadata"]["name"]: item
            for item in document.get("items", [])
            if item.get("spec", {}).get("transport") == "ssh"
        }

    def names(self) -> list[str]:
        return sorted(self.targets)

    def route(self, name: str) -> tuple[str, str]:
        try:
            spec = self.targets[name]["spec"]
        except KeyError as exc:
            raise BrokerError("TARGET_NOT_FOUND", name) from exc
        fabric = spec.get("cluster", {}).get("fabric", {})
        # The accelerated management fabric is independent of Kubernetes API
        # availability. The declared address remains the fallback for targets
        # that are not members of that fabric.
        if fabric.get("acceleratedIP"):
            return str(fabric["acceleratedIP"]), "accelerated-management"
        if fabric.get("internalIP"):
            return str(fabric["internalIP"]), "private-management"
        return str(spec["address"]), "declared-management"


class SSHBroker:
    def __init__(self, registry: TargetRegistry, *, ssh_config: str, user: str = "re8ch-ai",
                 executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.registry = registry
        self.ssh_config = ssh_config
        self.user = user
        self.executor = executor

    def execute(self, name: str, argv: list[str], timeout: int = 30, *, actor: str = "") -> dict[str, Any]:
        if not argv:
            raise BrokerError("INVALID_ARGUMENT", "argv must not be empty")
        if timeout < 1 or timeout > 3600:
            raise BrokerError("INVALID_ARGUMENT", "timeout must be between 1 and 3600")
        address, route = self.registry.route(name)
        command = [shlex.join(argv)]
        command_hash = hashlib.sha256(command[0].encode()).hexdigest()
        operation_id = f"ssh_{uuid.uuid4().hex}"
        started = time.time()
        try:
            result = self.executor(
                ["ssh", "-F", self.ssh_config, "-l", self.user, "--", address, *command],
                text=True, capture_output=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BrokerError("TARGET_TIMEOUT", f"{name} timed out") from exc
        return {
            "operationId": operation_id,
            "target": name,
            "route": route,
            "actor": actor,
            "commandHash": command_hash,
            "exitCode": result.returncode,
            "stdout": result.stdout[-4_194_304:],
            "stderr": result.stderr[-262_144:],
            "truncated": len(result.stdout) > 4_194_304 or len(result.stderr) > 262_144,
            "durationMs": round((time.time() - started) * 1000),
            "auditRef": f"audit://ssh-broker/{operation_id}",
        }
