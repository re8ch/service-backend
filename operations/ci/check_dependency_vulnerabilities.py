#!/usr/bin/env python3
"""Enforce the repository's reviewed Trivy vulnerability baseline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


GATED_SEVERITIES = {"CRITICAL", "HIGH"}
SEVERITY_RANK = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _target(value: str) -> str:
    value = value.removeprefix("/work/").removeprefix("./")
    return value


def _key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (_target(item["target"]), item["package"], item["vulnerabilityId"])


def findings_from_report(report: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for result in report.get("Results") or []:
        target = _target(result.get("Target", ""))
        for vulnerability in result.get("Vulnerabilities") or []:
            severity = vulnerability.get("Severity", "UNKNOWN").upper()
            findings.append(
                {
                    "target": target,
                    "package": vulnerability.get("PkgName", ""),
                    "vulnerabilityId": vulnerability.get("VulnerabilityID", ""),
                    "severity": severity,
                    "installedVersion": vulnerability.get("InstalledVersion", ""),
                    "fixedVersion": vulnerability.get("FixedVersion", ""),
                }
            )
    return sorted(findings, key=_key)


def evaluate(
    report: dict[str, Any], baseline: dict[str, Any], today: dt.date
) -> tuple[list[str], str]:
    if baseline.get("schemaVersion") != 1:
        raise ValueError("unsupported baseline schemaVersion")

    findings = findings_from_report(report)
    gated = { _key(item): item for item in findings if item["severity"] in GATED_SEVERITIES }
    exceptions = baseline.get("exceptions") or []
    reviewed: dict[tuple[str, str, str], dict[str, Any]] = {}
    errors: list[str] = []

    for exception in exceptions:
        key = _key(exception)
        if key in reviewed:
            errors.append(f"duplicate baseline exception: {' / '.join(key)}")
            continue
        reviewed[key] = exception

    for key, finding in gated.items():
        exception = reviewed.get(key)
        label = " / ".join(key)
        if exception is None:
            errors.append(f"new {finding['severity']} vulnerability: {label}")
            continue
        expires_on = dt.date.fromisoformat(exception["expiresOn"])
        if today > expires_on:
            errors.append(f"expired baseline exception ({expires_on.isoformat()}): {label}")
        if finding["installedVersion"] != exception.get("installedVersion"):
            errors.append(
                "review required after installed version changed: "
                f"{label} ({exception.get('installedVersion')} -> {finding['installedVersion']})"
            )
        old_rank = SEVERITY_RANK.get(str(exception.get("severity", "UNKNOWN")).upper(), 0)
        new_rank = SEVERITY_RANK.get(finding["severity"], 0)
        if new_rank > old_rank:
            errors.append(
                f"severity increased: {label} ({exception.get('severity')} -> {finding['severity']})"
            )

    for key in sorted(set(reviewed) - set(gated)):
        errors.append(f"stale baseline exception; remove it: {' / '.join(key)}")

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    count_text = ", ".join(
        f"{severity}={counts.get(severity, 0)}"
        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
    )
    lines = [
        "## Dependency vulnerability policy",
        "",
        f"Trivy findings: {count_text}.",
        f"Reviewed CRITICAL/HIGH baseline entries: {len(reviewed)}.",
        f"Policy result: {'FAIL' if errors else 'PASS'}.",
    ]
    if errors:
        lines.extend(["", "Violations:", *[f"- {error}" for error in errors]])
    return errors, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()

    report = json.loads(args.report.read_text())
    baseline = json.loads(args.baseline.read_text())
    errors, summary = evaluate(report, baseline, args.today)
    print(summary, end="")
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a") as handle:
            handle.write(summary)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
