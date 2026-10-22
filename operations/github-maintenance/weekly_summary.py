#!/usr/bin/env python3
"""Publish one quiet, unassigned GitHub-native GitOps maintenance summary."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from collections import Counter
from typing import Any, Iterable


REPO = os.environ.get("GITHUB_REPOSITORY", "re8ch/service-backend")
OWNER = REPO.split("/", 1)[0]
PROJECT_NUMBER = os.environ.get("GITOPS_MAINTENANCE_PROJECT", "1")


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def api_pages(endpoint: str) -> list[dict[str, Any]]:
    pages = json.loads(gh("api", "--paginate", "--slurp", endpoint))
    return [item for page in pages for item in page]


def api_object(endpoint: str) -> dict[str, Any]:
    return json.loads(gh("api", endpoint))


def api_array(endpoint: str) -> list[dict[str, Any]]:
    """Fetch one deliberately bounded page without following historical links."""
    return json.loads(gh("api", endpoint))


def severity_counts(alerts: Iterable[dict[str, Any]], path: tuple[str, ...]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for alert in alerts:
        value: Any = alert
        for key in path:
            value = value.get(key, {}) if isinstance(value, dict) else {}
        counts[str(value or "unknown").lower()] += 1
    return counts


def format_counts(counts: Counter[str]) -> str:
    order = ("critical", "high", "medium", "moderate", "low", "warning", "note", "unknown")
    rendered = [f"{counts[level]} {level}" for level in order if counts[level]]
    return ", ".join(rendered) if rendered else "none"


def week_start(today: dt.date) -> dt.date:
    return today - dt.timedelta(days=today.weekday())


def collect() -> dict[str, Any]:
    dependabot_prs = [
        pr
        for pr in api_pages(f"repos/{REPO}/pulls?state=open&per_page=100")
        if pr.get("user", {}).get("login") == "dependabot[bot]"
    ]
    dependabot_alerts = api_pages(f"repos/{REPO}/dependabot/alerts?state=open&per_page=100")
    codeql_alerts = api_pages(f"repos/{REPO}/code-scanning/alerts?state=open&per_page=100")
    secret_alerts = api_pages(f"repos/{REPO}/secret-scanning/alerts?state=open&per_page=100")
    automation_issues = api_pages(
        f"repos/{REPO}/issues?state=open&labels=actor%2Fautomation&per_page=100"
    )

    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    workflow_runs = api_object(
        f"repos/{REPO}/actions/runs?created=%3E%3D{since}&per_page=100"
    ).get("workflow_runs", [])
    failed_runs = [
        run
        for run in workflow_runs
        if run.get("conclusion")
        in {"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"}
    ]

    deployments = api_array(f"repos/{REPO}/deployments?per_page=20")
    unhealthy_deployments: list[dict[str, Any]] = []
    for deployment in deployments:
        statuses = api_array(
            f"repos/{REPO}/deployments/{deployment['id']}/statuses?per_page=1"
        )
        if statuses and statuses[0].get("state") not in {"success", "inactive"}:
            unhealthy_deployments.append(
                {
                    "environment": deployment.get("environment", "unknown"),
                    "state": statuses[0].get("state", "unknown"),
                }
            )

    return {
        "dependabot_prs": dependabot_prs,
        "dependabot_alerts": dependabot_alerts,
        "codeql_alerts": codeql_alerts,
        "secret_alerts": secret_alerts,
        "automation_issues": [item for item in automation_issues if "pull_request" not in item],
        "failed_runs": failed_runs,
        "unhealthy_deployments": unhealthy_deployments,
    }


def render(data: dict[str, Any], period: dt.date) -> str:
    dependabot = severity_counts(data["dependabot_alerts"], ("security_advisory", "severity"))
    codeql = severity_counts(data["codeql_alerts"], ("rule", "security_severity_level"))

    failed = "\n".join(
        f"- [{run.get('name', 'workflow')}]({run.get('html_url')}) — {run.get('conclusion')}"
        for run in data["failed_runs"][:10]
    ) or "- None"
    deployment_lines = "\n".join(
        f"- `{item['environment']}` — {item['state']}"
        for item in data["unhealthy_deployments"][:10]
    ) or "- None"

    return f"""## Weekly GitOps maintenance summary

Period beginning: `{period.isoformat()}`

| Signal | Open/current state |
| --- | ---: |
| Dependabot update PRs | {len(data['dependabot_prs'])} |
| Dependabot alerts | {len(data['dependabot_alerts'])} ({format_counts(dependabot)}) |
| CodeQL alerts | {len(data['codeql_alerts'])} ({format_counts(codeql)}) |
| Secret scanning alerts | {len(data['secret_alerts'])} |
| Open automation maintenance Issues | {len(data['automation_issues'])} |
| Failed/cancelled Actions in the last 7 days | {len(data['failed_runs'])} |
| Recent deployments not in success/inactive | {len(data['unhealthy_deployments'])} |

### Recent failed or cancelled Actions

{failed}

### Deployment observations requiring reconciliation

{deployment_lines}

### Notification policy

This report is generated by the Maintenance App without assignees, mentions, or
review requests. Routine reconciliation samples remain quiet. Critical security
signals, protected deployment failures, observer SLO loss, and changes to
CODEOWNERS-protected authority paths follow their dedicated escalation paths.
"""


def publish(title: str, body: str) -> str:
    existing = api_pages(f"repos/{REPO}/issues?state=all&labels=maintenance-summary&per_page=100")
    match = next((issue for issue in existing if issue.get("title") == title), None)
    if match:
        gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{REPO}/issues/{match['number']}",
            "-f",
            f"body={body}",
        )
        return str(match["html_url"])

    created = json.loads(
        gh(
            "api",
            "--method",
            "POST",
            f"repos/{REPO}/issues",
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
            "-f",
            "labels[]=type/maintenance",
            "-f",
            "labels[]=actor/automation",
            "-f",
            "labels[]=maintenance-summary",
        )
    )
    issue_url = str(created["html_url"])
    gh("project", "item-add", PROJECT_NUMBER, "--owner", OWNER, "--url", issue_url)
    return issue_url


def main() -> None:
    period = week_start(dt.datetime.now(dt.timezone.utc).date())
    title = f"GitOps maintenance summary · {period.isoformat()}"
    print(publish(title, render(collect(), period)))


if __name__ == "__main__":
    main()
