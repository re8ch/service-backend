#!/usr/bin/env python3
"""Fail closed on incomplete image scans or unreviewed HIGH/CRITICAL findings."""

import argparse
import json
from pathlib import Path


GATED = {"HIGH", "CRITICAL"}


def findings(source: str, report: dict) -> list[dict]:
    if source == "harbor":
        overview = report.get("scan_overview")
        payload = report.get("vulnerability_report")
        if not isinstance(overview, dict) or not overview or not all(
            isinstance(item, dict) and item.get("scan_status") == "Success"
            for item in overview.values()
        ):
            raise ValueError("Harbor scan did not complete successfully")
        if not isinstance(payload, dict):
            raise ValueError("Harbor vulnerability report is missing")
        reports = [item["vulnerabilities"] for item in payload.values()
                   if isinstance(item, dict) and isinstance(item.get("vulnerabilities"), list)]
        if not reports:
            raise ValueError("Harbor vulnerability report is incomplete")
        return [finding for items in reports for finding in items]
    if source == "trivy":
        if report.get("SchemaVersion", 0) < 2 or report.get("ArtifactType") != "container_image":
            raise ValueError("Trivy image report is missing or incomplete")
        results = report.get("Results")
        if not isinstance(results, list):
            raise ValueError("Trivy vulnerability results are missing")
        return [finding for result in results for finding in result.get("Vulnerabilities") or []]
    raise ValueError("unsupported scan source")


def evaluate(source: str, paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        report = json.loads(path.read_text())
        for item in findings(source, report):
            severity = str(item.get("severity", item.get("Severity", "UNKNOWN"))).upper()
            if severity in GATED:
                identifier = item.get("id", item.get("VulnerabilityID", "unknown"))
                errors.append(f"{path.name}: {severity} {identifier}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=("harbor", "trivy"))
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    if len(args.reports) != 2:
        parser.error("both Commerce and payment-registry image reports are required")
    try:
        errors = evaluate(args.source, args.reports)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"image scan validation failed: {error}")
        return 1
    if errors:
        print("image scan found release-blocking vulnerabilities:")
        for error in errors:
            print(error)
        return 1
    print(f"{args.source} image scan complete for both images; no HIGH/CRITICAL findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
