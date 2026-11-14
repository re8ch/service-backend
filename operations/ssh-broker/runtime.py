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
        return self.routes(name)[0]

    def routes(self, name: str) -> list[tuple[str, str]]:
        try:
            spec = self.targets[name]["spec"]
        except KeyError as exc:
            raise BrokerError("TARGET_NOT_FOUND", name) from exc
        fabric = spec.get("cluster", {}).get("fabric", {})
        # Route only through reviewed inventory values. Prefer the accelerated
        # fabric, but retain the private and declared management paths so a
        # stale accelerated route cannot make an otherwise healthy node
        # unreachable.
        routes: list[tuple[str, str]] = []
        if fabric.get("acceleratedIP"):
            routes.append((str(fabric["acceleratedIP"]), "accelerated-management"))
        if fabric.get("internalIP"):
            routes.append((str(fabric["internalIP"]), "private-management"))
        routes.append((str(spec["address"]), "declared-management"))
        return list(dict.fromkeys(routes))


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
        command = [shlex.join(argv)]
        command_hash = hashlib.sha256(command[0].encode()).hexdigest()
        operation_id = f"ssh_{uuid.uuid4().hex}"
        started = time.time()
        attempts: list[str] = []
        result: subprocess.CompletedProcess[str] | None = None
        route = ""
        connectivity_errors = (
            "operation timed out", "connection timed out", "no route to host",
            "network is unreachable", "connection refused", "could not resolve hostname",
        )
        for address, candidate_route in self.registry.routes(name):
            remaining = timeout - (time.time() - started)
            if remaining <= 0:
                break
            attempts.append(candidate_route)
            route = candidate_route
            try:
                result = self.executor(
                    ["ssh", "-F", self.ssh_config, "-l", self.user, "--", address, *command],
                    text=True, capture_output=True, timeout=remaining, check=False,
                )
            except subprocess.TimeoutExpired:
                result = None
                continue
            if result.returncode == 0:
                break
            if result.returncode != 255 or not any(message in result.stderr.lower() for message in connectivity_errors):
                break
        if result is None:
            raise BrokerError("TARGET_TIMEOUT", f"{name} timed out")
        return {
            "operationId": operation_id,
            "target": name,
            "route": route,
            "attemptedRoutes": attempts,
            "actor": actor,
            "commandHash": command_hash,
            "exitCode": result.returncode,
            "stdout": result.stdout[-4_194_304:],
            "stderr": result.stderr[-262_144:],
            "truncated": len(result.stdout) > 4_194_304 or len(result.stderr) > 262_144,
            "durationMs": round((time.time() - started) * 1000),
            "auditRef": f"audit://ssh-broker/{operation_id}",
        }
