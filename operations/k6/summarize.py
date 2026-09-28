#!/usr/bin/env python3
"""Render a k6 JSON summary as a compact GitHub Actions result."""

import argparse
import json
from pathlib import Path


def render(summary, cadence):
    metrics = summary["metrics"]
    checks = summary["root_group"]["checks"]
    total = metrics.get("checks", {})
    latency = metrics.get("http_req_duration", {})
    failures = metrics.get("contract_failures", {})
    requests = metrics.get("http_reqs", {})
    lines = [
        "### k6 public functional contracts",
        f"Cadence: `{cadence}` · Requests: **{requests.get('count', 0)}** · "
        f"Checks: **{total.get('passes', 0)} passed / {total.get('fails', 0)} failed**",
        f"Contract failure rate: **{failures.get('value', 0):.1%}** · "
        f"HTTP p95: **{latency.get('p(95)', 0):.0f} ms**",
        "",
        "| Contract | Passed | Failed |",
        "| --- | ---: | ---: |",
    ]
    for name, result in sorted(checks.items()):
        lines.append(f"| {name} | {result['passes']} | {result['fails']} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--cadence", choices=("fast", "standard", "all"), required=True)
    args = parser.parse_args()
    print(render(json.loads(args.summary.read_text(encoding="utf-8")), args.cadence), end="")


if __name__ == "__main__":
    main()
