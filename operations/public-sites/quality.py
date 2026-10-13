#!/usr/bin/env python3
"""Plan and summarize non-blocking Lighthouse audits for governed websites."""

from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "inventory/public-sites.yaml"
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
CATEGORY_PROFILE_KEYS = {"best-practices": "bestPractices"}


def load_catalog() -> dict:
    return yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["spec"]


def matrix(spec: dict) -> list[dict]:
    rows = []
    for site in spec["sites"]:
        profile = spec["qualityProfiles"][site["qualityProfile"]]
        for deployment in site["deployments"]:
            rows.append({
                "site": site["id"],
                "deployment": deployment["id"],
                "url": deployment["primaryUrl"],
                "runs": profile["runs"],
            })
    return rows


def summarize(spec: dict, site_id: str, deployment_id: str, reports: list[dict]) -> tuple[str, bool]:
    site = next(site for site in spec["sites"] if site["id"] == site_id)
    deployment = next(item for item in site["deployments"] if item["id"] == deployment_id)
    profile = spec["qualityProfiles"][site["qualityProfile"]]
    scores = {
        category: round(statistics.median(report["categories"][category]["score"] * 100))
        for category in CATEGORIES
    }
    audits = {
        "lcpMs": round(statistics.median(report["audits"]["largest-contentful-paint"]["numericValue"] for report in reports)),
        "cls": round(statistics.median(report["audits"]["cumulative-layout-shift"]["numericValue"] for report in reports), 3),
        "tbtMs": round(statistics.median(report["audits"]["total-blocking-time"]["numericValue"] for report in reports)),
    }
    failures = [
        f"{category} {scores[category]} < {profile['lighthouse'][CATEGORY_PROFILE_KEYS.get(category, category)]}"
        for category in CATEGORIES
        if scores[category] < profile["lighthouse"][CATEGORY_PROFILE_KEYS.get(category, category)]
    ]
    for metric, value in audits.items():
        if value > profile["webVitals"][metric]:
            failures.append(f"{metric} {value} > {profile['webVitals'][metric]}")
    lines = [
        f"### {site['displayName']} / {deployment_id}",
        "",
        f"URL: {deployment['primaryUrl']}",
        "",
        "| Metric | Median | Target |",
        "| --- | ---: | ---: |",
    ]
    for category in CATEGORIES:
        key = CATEGORY_PROFILE_KEYS.get(category, category)
        lines.append(f"| {category} | {scores[category]} | ≥ {profile['lighthouse'][key]} |")
    lines.extend([
        f"| LCP | {audits['lcpMs']} ms | ≤ {profile['webVitals']['lcpMs']} ms |",
        f"| CLS | {audits['cls']} | ≤ {profile['webVitals']['cls']} |",
        f"| TBT | {audits['tbtMs']} ms | ≤ {profile['webVitals']['tbtMs']} ms |",
        "",
        "Advisory result: " + ("target met" if not failures else "; ".join(failures)),
    ])
    return "\n".join(lines) + "\n", not failures


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("matrix")
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--site", required=True)
    report_parser.add_argument("--deployment", required=True)
    report_parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    spec = load_catalog()
    if args.command == "matrix":
        payload = json.dumps({"include": matrix(spec)}, separators=(",", ":"))
        print(payload)
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"matrix={payload}\n")
        return
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    markdown, passed = summarize(spec, args.site, args.deployment, reports)
    print(markdown)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
