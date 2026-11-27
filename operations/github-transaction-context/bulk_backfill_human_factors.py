#!/usr/bin/env python3
"""Bulk-link detached human PRs to central lifecycle factor Issues."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


FACTOR_REPOSITORY = "re8ch/service-backend"
MIGRATION_ISSUE = 3127
MARKER_PREFIX = "transaction-factor-backfill:source="


def targets(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (
            pr for pr in ledger.get("pullRequests", [])
            if pr.get("detached")
            and pr.get("author", {}).get("kind") == "human"
            and pr.get("repository") != FACTOR_REPOSITORY
        ),
        key=lambda pr: (pr["repository"].casefold(), pr["number"]),
    )


def marker(pr: dict[str, Any]) -> str:
    return f"<!-- {MARKER_PREFIX}{pr['repository']}#{pr['number']} -->"


def canonical_repository(pr: dict[str, Any]) -> str:
    parsed = urllib.parse.urlparse(pr["url"])
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc != "github.com" or len(parts) < 4 or parts[2] != "pull":
        raise ValueError(f"unsupported pull request URL: {pr['url']}")
    return f"{parts[0]}/{parts[1]}"


def factor_title(pr: dict[str, Any]) -> str:
    title = " ".join(pr["title"].split())
    return f"Track {pr['repository']}#{pr['number']}: {title}"[:240]


def issue_type(pr: dict[str, Any]) -> str:
    title = pr["title"].casefold()
    if title.startswith(("fix", "bug")) or " fix " in f" {title} ":
        return "Bug"
    if title.startswith(("feat", "add", "implement", "publish", "enable")):
        return "Feature"
    return "Task"


def factor_body(pr: dict[str, Any]) -> str:
    return f"""## Outcome
Own the lifecycle and acceptance context for [{pr['repository']}#{pr['number']}]({pr['url']}): {pr['title']}

## Context
- Parent Issue: none
- Source repository: `{pr['repository']}`
- Source PR: {pr['url']}
- Migration wave: #{MIGRATION_ISSUE}
- Original author: `{pr['author']['login']}`
- Original created date: `{pr['createdAt']}`

## Acceptance
- [ ] Source PR checks and review are complete
- [ ] Runtime or delivery evidence is linked when applicable
- [ ] Source PR remains the implementation authority

## Evidence
This factor was created by the reviewed historical PR backfill. Merging the source PR closes this Issue through GitHub's native cross-repository relationship.

{marker(pr)}
"""


def append_closing_reference(body: str | None, factor: int, pr: dict[str, Any]) -> str:
    body = (body or "").rstrip()
    backfill_marker = marker(pr)
    if backfill_marker in body:
        return body + "\n"
    suffix = (
        f"\n\n## Transaction factor\n\n"
        f"Closes {FACTOR_REPOSITORY}#{factor}\n\n{backfill_marker}\n"
    )
    return body + suffix


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"https://api.github.com{path}", data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "re8ch-human-factor-backfill",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error


def apply(ledger: dict[str, Any], github: GitHub, checkpoint: Path) -> dict[str, Any]:
    result = {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 0, "transactions": []}
    previous: dict[str, Any] = {}
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        previous = {row["source"]: row for row in saved.get("transactions", [])}

    for pr in targets(ledger):
        source = f"{pr['repository']}#{pr['number']}"
        result["attempted"] += 1
        if source in previous and previous[source].get("status") == "succeeded":
            result["skipped"] += 1
            result["transactions"].append(previous[source])
            continue
        row = {"source": source, "url": pr["url"], "status": "failed"}
        try:
            old_factor = (previous.get(source) or {}).get("factor")
            if old_factor:
                factor_number = int(old_factor.rsplit("#", 1)[1])
            else:
                factor = github.request("POST", f"/repos/{FACTOR_REPOSITORY}/issues", {
                    "title": factor_title(pr),
                    "body": factor_body(pr),
                    "type": issue_type(pr),
                })
                factor_number = factor["number"]
            row["factor"] = f"{FACTOR_REPOSITORY}#{factor_number}"
            repository = canonical_repository(pr)
            current = github.request("GET", f"/repos/{repository}/pulls/{pr['number']}")
            github.request("PATCH", f"/repos/{repository}/pulls/{pr['number']}", {
                "body": append_closing_reference(current.get("body"), factor_number, pr),
            })
            row["status"] = "succeeded"
            result["succeeded"] += 1
        except Exception as error:  # keep independent transactions moving
            row["error"] = str(error)
            result["failed"] += 1
        result["transactions"].append(row)
        checkpoint.write_text(json.dumps(result, indent=2) + "\n")
        time.sleep(0.15)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ledger = json.loads(args.ledger.read_text())
    plan = {"targetCount": len(targets(ledger)), "targets": [f"{p['repository']}#{p['number']}" for p in targets(ledger)]}
    if not args.apply:
        args.output.write_text(json.dumps(plan, indent=2) + "\n")
        print(json.dumps({"mode": "plan", "targetCount": plan["targetCount"]}))
        return 0
    result = apply(ledger, GitHub(os.environ["GH_TOKEN"]), args.output)
    print(json.dumps({key: result[key] for key in ("attempted", "succeeded", "failed", "skipped")}))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
