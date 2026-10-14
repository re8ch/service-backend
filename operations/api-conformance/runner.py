#!/usr/bin/env python3
"""Bounded Schemathesis runner. Stdout contains one sanitized JSON object only."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit
from pathlib import Path

import yaml

from contract import ContractError, sanitized_summary, schema_digest, validate_document, validate_target
from request_budget import initialize as initialize_budget, take as take_budget

MAX_SCHEMA_BYTES = 4 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContractError("schema redirects are disabled")


class UncertainMutation(ContractError):
    """The request may have reached the server but no response was received."""


def fetch_schema(target: dict) -> bytes:
    request = urllib.request.Request(target["schema"]["url"], headers={"Accept": "application/json, application/yaml"})
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=20) as response:
        length = int(response.headers.get("Content-Length", 0) or 0)
        if length > MAX_SCHEMA_BYTES:
            raise ContractError("schema is too large")
        raw = response.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            raise ContractError("schema is too large")
        return raw


def template(value, variables: dict[str, str]):
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("${" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [template(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: template(item, variables) for key, item in value.items()}
    return value


def extract(body: object, path: str):
    current = body
    for part in path.removeprefix("$.").split("."):
        if not isinstance(current, dict) or part not in current:
            raise ContractError("lifecycle capture path did not resolve")
        current = current[part]
    if not isinstance(current, (str, int, float, bool)):
        raise ContractError("captured lifecycle value must be scalar")
    return str(current)


def lifecycle_request(target: dict, step: dict, variables: dict[str, str], *, compensation=False) -> None:
    method = step["method"].upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ContractError("invalid lifecycle method")
    path = template(step["path"], variables)
    parsed = urlsplit(path)
    if (not path.startswith("/") or path.startswith("//") or parsed.scheme
            or parsed.netloc or parsed.fragment or "\\" in unquote(path)
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))):
        raise ContractError("invalid lifecycle path")
    operations = target.get("operations", {})
    allowed = operations.get("readOperationIds", []) if method in {"GET", "HEAD", "OPTIONS"} else operations.get("mutationOperationIds", [])
    if step.get("operationId") not in allowed:
        raise ContractError("lifecycle operation is not allowlisted")
    url = target["baseUrl"].rstrip("/") + path
    body = template(step.get("body"), variables)
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "X-RE8CH-Synthetic": "true", "X-RE8CH-Run-ID": variables["RUN_ID"]}
    if raw is not None:
        headers["Content-Type"] = "application/json"
    if authorization := os.getenv("AUTHORIZATION"):
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, data=raw, method=method, headers=headers)
    if budget := os.getenv("RE8CH_REQUEST_BUDGET"):
        take_budget(budget, compensation=compensation)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, TimeoutError) as exc:
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            raise UncertainMutation("mutation response was not observed") from exc
        raise
    payload = response.read(1024 * 1024)
    if response.status not in step.get("expectedStatuses", [200]):
        raise ContractError(f"lifecycle step returned unexpected status {response.status}")
    if step.get("capture"):
        parsed = json.loads(payload or b"{}")
        for name, path in step["capture"].items():
            variables[name] = extract(parsed, path)


def run_steps(target: dict, key: str, variables: dict[str, str]) -> str:
    steps = target.get("lifecycle", {}).get(key, [])
    if not steps:
        return "not_run"
    for step in steps:
        lifecycle_request(target, step, variables, compensation=key in {"cleanup", "reversal"})
    return "passed"


def parse_document(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ContractError("invalid OpenAPI document") from exc
        if not isinstance(document, dict):
            raise ContractError("invalid OpenAPI document")
        return document


def main() -> int:
    target_name = os.environ["TARGET_NAME"]
    run_id = os.environ["RUN_ID"]
    mode = os.getenv("RUN_MODE", "read")
    seed = int(os.getenv("SEED", str(int(time.time()))))
    target = validate_target(json.loads(os.environ["TARGET_JSON"]))
    raw = fetch_schema(target)
    digest = schema_digest(raw)
    if digest != target["schema"]["sha256"]:
        raise ContractError("schema digest drift")
    document = parse_document(raw)
    validate_document(document, target)
    configured = target["operations"]
    operation_ids = configured.get("readOperationIds", []) if mode == "read" else configured.get("mutationOperationIds", [])
    if mode == "mutation" and not operation_ids:
        raise ContractError("mutation run has no allowlisted operations")
    if not operation_ids:
        raise ContractError("run has no allowlisted operations")
    lifecycle = {"setup": "not_run", "test": "not_run", "cleanup": "not_run", "reversal": "not_run"}
    variables = {"RUN_ID": run_id, "TARGET": target_name}
    report = None
    exit_code = 1
    audit_refs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="re8ch-schemathesis-") as directory:
        limits = target.get("limits", {})
        reserved = sum(len(target.get("lifecycle", {}).get(key, [])) for key in ("cleanup", "reversal")) if mode == "mutation" else 0
        budget_path = Path(directory) / "request-budget.json"
        initialize_budget(budget_path, int(limits.get("maxRequests", 500)), reserved,
                          int(limits.get("ratePerSecond", 1)))
        take_budget(budget_path)  # Account for the already completed schema GET.
        os.environ["RE8CH_REQUEST_BUDGET"] = str(budget_path)
        schema_path = Path(directory) / "openapi.json"
        report_path = Path(directory) / "report.json"
        config_path = Path(directory) / "schemathesis.toml"
        # Generation DB=none does not disable Schemathesis' separate crash
        # cache. Never persist request/response examples or replay commands.
        config_path.write_text("[cache]\nenabled = false\n")
        schema_path.write_bytes(raw)
        try:
            if mode == "mutation":
                lifecycle["setup"] = run_steps(target, "setup", variables)
            pattern = "^(?:" + "|".join(re.escape(item) for item in operation_ids) + ")$"
            limits = target.get("limits", {})
            rate = int(limits.get("ratePerSecond", 1))
            request_budget = int(limits.get("maxRequests", 500))
            # Keep one finite generation cycle. In 4.29.1 --max-time repeats
            # fuzzing until time runs out, even after max-examples is reached.
            # A separate shared transport counter enforces the request budget.
            effective_timeout = min(int(limits.get("timeoutSeconds", 600)), max(1, request_budget // rate))
            command = [
                "schemathesis", "--config-file", str(config_path), "run", str(schema_path), "--url", target["baseUrl"],
                "--include-operation-id-regex", pattern,
                "--checks", "all", "--workers", "1", "--max-examples", str(limits.get("maxExamples", 25)),
                "--max-failures", "20",
                "--rate-limit", f"{rate}/s", "--max-redirects", "0",
                "--request-timeout", "15", "--request-retries", "0", "--seed", str(seed),
                "--generation-database", "none", "--output-sanitize", "true", "--output-truncate", "true",
                "--report", "json", "--report-json-path", str(report_path), "--no-color",
            ]
            command += ["--phases", "examples,coverage,fuzzing,stateful" if mode == "mutation" else "coverage,fuzzing"]
            if authorization := os.getenv("AUTHORIZATION"):
                command += ["--header", "Authorization:" + authorization]
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=effective_timeout, check=False,
                                       env={**os.environ, "SCHEMATHESIS_HOOKS": "budget_hooks",
                                            "PYTHONPATH": str(Path(__file__).parent)})
            exit_code = completed.returncode
            if report_path.exists():
                report = json.loads(report_path.read_text())
            lifecycle["test"] = "passed" if exit_code == 0 else "failed"
        finally:
            if mode == "mutation":
                try:
                    lifecycle["reversal"] = run_steps(target, "reversal", variables)
                except Exception:
                    lifecycle["reversal"] = "failed"
                    exit_code = 1
                try:
                    lifecycle["cleanup"] = run_steps(target, "cleanup", variables)
                except Exception:
                    lifecycle["cleanup"] = "failed"
                    exit_code = 1
    print(json.dumps(sanitized_summary(target=target_name, run_id=run_id, mode=mode, seed=seed,
                                       schema_sha256=digest, exit_code=exit_code, report=report,
                                       lifecycle=lifecycle, audit_refs=audit_refs), separators=(",", ":"), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Error classes are safe; exception messages may contain remote material and are never emitted.
        print(json.dumps({"version": 1, "status": "failed", "reason": type(exc).__name__}, separators=(",", ":")))
        raise SystemExit(1)
