#!/usr/bin/env python3
"""Turn sanitized NodeContract evidence into quiet, deduplicated governance."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any


REPO = os.environ.get("GITHUB_REPOSITORY", "re8ch/service-backend")
OWNER = REPO.split("/", 1)[0]
PROJECT_NUMBER = os.environ.get("GITOPS_MAINTENANCE_PROJECT", "1")
TITLE_PREFIX = "[NodeContract drift]"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def api(endpoint: str) -> Any:
    return json.loads(gh("api", endpoint))


def persistent_threshold_met() -> bool:
    current = str(os.environ.get("GITHUB_RUN_ID", ""))
    result = api(f"repos/{REPO}/actions/workflows/node-contract-observer.yml/runs?branch=main&per_page=20")
    completed = [
        run for run in result.get("workflow_runs", [])
        if str(run.get("id")) != current and run.get("status") == "completed"
    ]
    failure_streak: list[dict[str, Any]] = []
    for run in completed:
        if run.get("conclusion") != "failure":
            break
        failure_streak.append(run)
    consecutive = len(failure_streak) >= 2
    now = dt.datetime.now(dt.timezone.utc)
    continuous_24h = bool(failure_streak) and (
        now - dt.datetime.fromisoformat(failure_streak[-1]["created_at"].replace("Z", "+00:00")) >= dt.timedelta(hours=24)
    )
    return consecutive or continuous_24h


def issue_key(drift: dict[str, Any]) -> str:
    parts = [drift["type"], drift.get("contractRef", "cluster"), drift.get("fieldRef", "")]
    return ":".join(part for part in parts if part)


def render_body(key: str, drift: dict[str, Any], evidence: dict[str, Any]) -> str:
    safe = json.dumps(drift, indent=2, sort_keys=True)
    return f"""## NodeContract reconciliation drift

Deduplication key: `{key}`

This issue was opened by the Maintenance App from sanitized observer evidence.
It has no assignee and intentionally mentions no user.

```json
{safe}
```

Expected nodes: `{evidence['expectedNodeCount']}`

Observed nodes: `{evidence['observedNodeCount']}`
Observer run: `{os.environ.get('GITHUB_RUN_ID', 'unknown')}`

Project status is governance metadata only and never grants Kubernetes access.
"""


def existing_issues() -> list[dict[str, Any]]:
    return [
        item for item in api(f"repos/{REPO}/issues?state=all&labels=actor%2Fautomation&per_page=100")
        if "pull_request" not in item and item.get("title", "").startswith(TITLE_PREFIX)
    ]


def publish(evidence: dict[str, Any]) -> None:
    persistent_ready = persistent_threshold_met() if any(item["severity"] == "persistent" for item in evidence["drift"]) else False
    actionable = {
        issue_key(item): item for item in evidence["drift"]
        if item["severity"] == "immediate" or persistent_ready
    }
    known = existing_issues()
    by_title = {item["title"]: item for item in known}
    active_titles: set[str] = set()
    for key, drift in sorted(actionable.items()):
        title = f"{TITLE_PREFIX} {key}"
        active_titles.add(title)
        body = render_body(key, drift, evidence)
        old = by_title.get(title)
        if old:
            gh("api", "--method", "PATCH", f"repos/{REPO}/issues/{old['number']}", "-f", f"body={body}", "-f", "state=open")
            continue
        created = json.loads(gh(
            "api", "--method", "POST", f"repos/{REPO}/issues",
            "-f", f"title={title}", "-f", f"body={body}",
            "-f", "labels[]=type/maintenance", "-f", "labels[]=actor/automation",
        ))
        gh("project", "item-add", PROJECT_NUMBER, "--owner", OWNER, "--url", created["html_url"])
    for old in known:
        if old.get("state") == "open" and old["title"] not in active_titles:
            gh("api", "--method", "PATCH", f"repos/{REPO}/issues/{old['number']}", "-f", "state=closed", "-f", "state_reason=completed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    publish(evidence)


if __name__ == "__main__":
    main()
