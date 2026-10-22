#!/usr/bin/env python3
"""Turn sanitized NodeContract evidence into quiet, deduplicated governance."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REPO = os.environ.get("GITHUB_REPOSITORY", "re8ch/service-backend")
OWNER = REPO.split("/", 1)[0]
PROJECT_NUMBER = os.environ.get("GITOPS_MAINTENANCE_PROJECT", "1")
TITLE_PREFIX = "[NodeContract drift]"
ACTIVE_TITLE = f"{TITLE_PREFIX} active reconciliation"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def api(endpoint: str) -> Any:
    return json.loads(gh("api", endpoint))


def historical_evidence() -> list[tuple[dt.datetime, dict[str, Any]]]:
    """Return the uninterrupted history of prior, valid observer evidence.

    A failed run without an evidence artifact breaks the streak. This prevents
    transport, authentication, or workflow failures from being misclassified as
    repeated declaration drift.
    """
    current = str(os.environ.get("GITHUB_RUN_ID", ""))
    result = api(f"repos/{REPO}/actions/workflows/node-contract-observer.yml/runs?branch=main&per_page=50")
    completed = [
        run for run in result.get("workflow_runs", [])
        if str(run.get("id")) != current and run.get("status") == "completed"
    ]
    history: list[tuple[dt.datetime, dict[str, Any]]] = []
    for run in completed:
        run_id = str(run["id"])
        artifact_name = f"node-contract-evidence-{run_id}"
        artifacts = api(f"repos/{REPO}/actions/runs/{run_id}/artifacts").get("artifacts", [])
        if not any(item.get("name") == artifact_name and not item.get("expired") for item in artifacts):
            break
        with tempfile.TemporaryDirectory(prefix="node-contract-history-") as directory:
            try:
                gh("run", "download", run_id, "--repo", REPO, "--name", artifact_name, "--dir", directory)
                evidence = json.loads((Path(directory) / "node-contract-evidence.json").read_text(encoding="utf-8"))
            except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
                break
        created_at = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        history.append((created_at, evidence))
    return history


def persistent_keys(
    evidence: dict[str, Any],
    history: list[tuple[dt.datetime, dict[str, Any]]],
    *,
    now: dt.datetime | None = None,
) -> set[str]:
    """Select drift keys seen in 3 valid observations or continuously for 24h."""
    current = {
        issue_key(item) for item in evidence["drift"]
        if item["severity"] == "persistent"
    }
    if not current:
        return set()
    now = now or dt.datetime.now(dt.timezone.utc)
    occurrences = {key: 1 for key in current}
    oldest = {key: now for key in current}
    candidates = set(current)
    for created_at, previous in history:
        previous_keys = {
            issue_key(item) for item in previous.get("drift", [])
            if item.get("severity") == "persistent"
        }
        candidates &= previous_keys
        if not candidates:
            break
        for key in candidates:
            occurrences[key] += 1
            oldest[key] = created_at
    return {
        key for key in current
        if occurrences[key] >= 3 or now - oldest[key] >= dt.timedelta(hours=24)
    }


def issue_key(drift: dict[str, Any]) -> str:
    parts = [drift["type"], drift.get("contractRef", "cluster"), drift.get("fieldRef", "")]
    return ":".join(part for part in parts if part)


def render_body(actionable: list[dict[str, Any]], evidence: dict[str, Any]) -> str:
    breakdown: dict[tuple[str, str], int] = {}
    for drift in actionable:
        key = (drift["severity"], drift["type"])
        breakdown[key] = breakdown.get(key, 0) + 1
    summary = "\n".join(
        f"- `{severity}/{kind}`: {count}"
        for (severity, kind), count in sorted(breakdown.items())
    )
    return f"""## NodeContract reconciliation drift

This issue was opened by the Maintenance App from sanitized observer evidence.
It has no assignee and intentionally mentions no user.

Actionable drift count: `{len(actionable)}`

{summary}

Expected nodes: `{evidence['expectedNodeCount']}`

Observed nodes: `{evidence['observedNodeCount']}`
Observer run: `{os.environ.get('GITHUB_RUN_ID', 'unknown')}`

Per-node details remain only in the access-controlled Actions artifact.

Project status is governance metadata only and never grants Kubernetes access.
"""


def existing_issues() -> list[dict[str, Any]]:
    return [
        item for item in api(f"repos/{REPO}/issues?state=all&labels=actor%2Fautomation&per_page=100")
        if "pull_request" not in item and item.get("title", "").startswith(TITLE_PREFIX)
    ]


def publish(evidence: dict[str, Any]) -> None:
    eligible = persistent_keys(evidence, historical_evidence())
    actionable = [
        item for item in evidence["drift"]
        if item["severity"] == "immediate" or issue_key(item) in eligible
    ]
    known = existing_issues()
    by_title = {item["title"]: item for item in known}
    active_titles: set[str] = set()
    if actionable:
        active_titles.add(ACTIVE_TITLE)
        body = render_body(actionable, evidence)
        old = by_title.get(ACTIVE_TITLE)
        if old:
            gh("api", "--method", "PATCH", f"repos/{REPO}/issues/{old['number']}", "-f", f"body={body}", "-f", "state=open")
        else:
            created = json.loads(gh(
                "api", "--method", "POST", f"repos/{REPO}/issues",
                "-f", f"title={ACTIVE_TITLE}", "-f", f"body={body}",
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
