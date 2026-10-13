#!/usr/bin/env python3
"""Pure validation and redaction helpers for API conformance runs."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse

SAFE_METHODS = frozenset({"get", "head", "options"})
MUTATION_METHODS = frozenset({"post", "put", "patch", "delete"})
SECRET_KEYS = ("authorization", "cookie", "token", "secret", "password", "private", "credential")
MAX_REPORT_BYTES = 60 * 1024


class ContractError(ValueError):
    pass


def _require(value: bool, message: str) -> None:
    if not value:
        raise ContractError(message)


def validate_target(spec: dict) -> dict:
    """Validate a target without resolving credentials or making requests."""
    repository = spec.get("repository", "")
    _require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)), "invalid repository")
    base = urlparse(spec.get("baseUrl", ""))
    schema = urlparse(spec.get("schema", {}).get("url", ""))
    _require(base.scheme == "https" and bool(base.hostname), "baseUrl must be HTTPS")
    _require(schema.scheme == "https" and bool(schema.hostname), "schema URL must be HTTPS")
    allowed = set(spec.get("network", {}).get("allowedHosts", []))
    _require(bool(allowed) and base.hostname in allowed and schema.hostname in allowed, "host is not allowlisted")
    digest = spec.get("schema", {}).get("sha256", "")
    _require(bool(re.fullmatch(r"[a-f0-9]{64}", digest)), "schema digest must be sha256")
    limits = spec.get("limits", {})
    _require(1 <= int(limits.get("maxExamples", 25)) <= 100, "maxExamples out of range")
    _require(1 <= int(limits.get("maxRequests", 500)) <= 2000, "maxRequests out of range")
    _require(1 <= int(limits.get("ratePerSecond", 1)) <= 5, "ratePerSecond out of range")
    _require(60 <= int(limits.get("timeoutSeconds", 600)) <= 900, "timeoutSeconds out of range")
    mutations = spec.get("operations", {}).get("mutationOperationIds", [])
    if mutations:
        _require(spec.get("canary", {}).get("tenantRef", "").startswith("schemathesis-canary"),
                 "mutations require a dedicated canary tenant")
        _require(bool(spec.get("lifecycle", {}).get("cleanup")), "mutations require cleanup steps")
        _require(bool(spec.get("operations", {}).get("allowGeneratedMutations") is True),
                 "generated mutations require an explicit opt-in")
    return spec


def validate_document(document: dict, target: dict) -> dict[str, tuple[str, str]]:
    """Reject remote references and return operationId -> (method, path)."""
    _require(isinstance(document, dict) and isinstance(document.get("paths"), dict), "invalid OpenAPI document")
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True)
    for match in re.finditer(r'"\$ref":"([^"]+)"', encoded):
        _require(match.group(1).startswith("#/"), "only document-local OpenAPI references are allowed")
    operations: dict[str, tuple[str, str]] = {}
    for path, item in document["paths"].items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in SAFE_METHODS | MUTATION_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            _require(isinstance(operation_id, str) and operation_id, f"missing operationId: {method} {path}")
            _require(operation_id not in operations, f"duplicate operationId: {operation_id}")
            operations[operation_id] = (method.lower(), path)
    configured = target.get("operations", {})
    for operation_id in configured.get("readOperationIds", []) + configured.get("mutationOperationIds", []):
        _require(operation_id in operations, f"unknown operationId: {operation_id}")
    for operation_id in configured.get("readOperationIds", []):
        _require(operations[operation_id][0] in SAFE_METHODS, f"read operation is mutating: {operation_id}")
    for operation_id in configured.get("mutationOperationIds", []):
        _require(operations[operation_id][0] in MUTATION_METHODS, f"mutation operation is not mutating: {operation_id}")
    return operations


def schema_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def failure_fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def sanitized_summary(*, target: str, run_id: str, mode: str, seed: int, schema_sha256: str,
                      exit_code: int, report: dict | None, lifecycle: dict, audit_refs: list[str]) -> dict:
    """Project a potentially sensitive Schemathesis report into a bounded result."""
    report = report if isinstance(report, dict) else {}
    statistic = report.get("statistic", {}) if isinstance(report.get("statistic"), dict) else {}
    failures = report.get("failures", []) if isinstance(report.get("failures"), list) else []
    result = {
        "version": 1,
        "target": target,
        "runId": run_id,
        "mode": mode,
        "engine": "schemathesis",
        "engineVersion": "4.29.1",
        "seed": seed,
        "schemaSha256": schema_sha256,
        "status": "passed" if exit_code == 0 and lifecycle.get("cleanup") != "failed" and lifecycle.get("reversal") != "failed" else "failed",
        "exitCode": exit_code,
        "counts": {
            key: int(statistic.get(key, 0) or 0)
            for key in ("total", "passed", "failed", "errored", "skipped")
        },
        "failureFingerprints": sorted({failure_fingerprint(item) for item in failures})[:50],
        "lifecycle": {
            "setup": lifecycle.get("setup", "not_run"),
            "test": lifecycle.get("test", "not_run"),
            "cleanup": lifecycle.get("cleanup", "not_run"),
            "reversal": lifecycle.get("reversal", "not_run"),
        },
        "auditRefs": [ref for ref in audit_refs if re.fullmatch(r"audit://[A-Za-z0-9_./-]+", ref)][:20],
    }
    raw = json.dumps(result, separators=(",", ":"), sort_keys=True).encode()
    _require(len(raw) <= MAX_REPORT_BYTES, "sanitized report exceeds GitHub dispatch limit")
    _require(not any(secret in raw.decode().lower() for secret in SECRET_KEYS), "sensitive key leaked into report")
    return result
