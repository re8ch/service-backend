#!/usr/bin/env python3
"""Publish App-owned policy checks for trusted, fully checked PRs.

Run only from a protected default-branch workflow with an installation token.
Never execute code or workflow files from a pull request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

TRUSTED_AUTHORS = frozenset({"Woomou"})
ALLOWED_ORGS = frozenset({"public-edge", "advanced-fabrics", "among-clusters", "bifurcate-systems"})
ROLLOUT_CUTOFF = "2026-09-21T15:16:00Z"
CHECK_NAME = "re8ch-policy-reviewer"
APP_ID = 4973967


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise ValueError("installation token is required")
        self.token = token

    def request(self, path: str, *, method: str = "GET", data: dict | None = None) -> tuple[object, dict]:
        command = ["gh", "api", path, "-X", method, "-H", "Accept: application/vnd.github+json"]
        if data is not None:
            command.extend(["--input", "-"])
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            result = subprocess.run(
                command,
                input=None if data is None else json.dumps(data),
                text=True,
                capture_output=True,
                timeout=45,
                env={**os.environ, "GH_TOKEN": self.token},
                check=False,
            )
            if result.returncode == 0:
                return json.loads(result.stdout), {}
            if attempt == attempts - 1:
                raise RuntimeError(f"GitHub API request failed for {path}: {result.stderr.strip()[:300]}")
            time.sleep(attempt + 1)
        raise AssertionError("unreachable")

    def get(self, path: str) -> object:
        return self.request(path)[0]

    def pages(self, path: str) -> list:
        results = []
        for page in range(1, 101):
            join = "&" if "?" in path else "?"
            value = self.get(f"{path}{join}per_page=100&page={page}")
            items = value["repositories"] if isinstance(value, dict) and "repositories" in value else value
            if not isinstance(items, list):
                raise ValueError(f"expected a list from {path}")
            results.extend(items)
            if len(items) < 100:
                return results
        raise ValueError(f"pagination limit exceeded for {path}")


def checks_pass(api: GitHub, repo: str, sha: str) -> bool:
    checks = api.get(f"/repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100")
    runs = [run for run in checks["check_runs"] if not (
        run["name"] == CHECK_NAME and run.get("app", {}).get("id") == APP_ID
    )]
    if checks["total_count"] > 100 or not runs:
        return False
    if not any(run["conclusion"] == "success" for run in runs):
        return False
    if any(run["status"] != "completed" or run["conclusion"] not in ("success", "neutral", "skipped") for run in runs):
        return False
    status = api.get(f"/repos/{repo}/commits/{sha}/status")
    return status["state"] in ("success", "pending") and not any(
        entry["state"] != "success" for entry in status["statuses"]
    )


def eligible(api: GitHub, repo: dict, pr: dict) -> tuple[bool, str]:
    full_name = repo["full_name"]
    number = pr["number"]
    if pr["draft"] or pr["base"]["ref"] != repo["default_branch"]:
        return False, "draft or non-default base"
    if pr["created_at"] < ROLLOUT_CUTOFF:
        return False, "predates reviewer rollout"
    if pr["head"]["repo"] is None or pr["head"]["repo"]["full_name"] != full_name:
        return False, "fork or missing head repository"
    if pr["user"]["login"] not in TRUSTED_AUTHORS:
        return False, "untrusted PR author"
    commits = api.pages(f"/repos/{full_name}/pulls/{number}/commits")
    if not commits or any(
        not commit["commit"]["verification"]["verified"]
        or commit["author"] is None
        or commit["author"]["login"] != pr["user"]["login"]
        for commit in commits
    ):
        return False, "unsigned or untrusted commit"
    sha = pr["head"]["sha"]
    if not checks_pass(api, full_name, sha):
        return False, "checks not all complete and successful"
    reviews = api.pages(f"/repos/{full_name}/pulls/{number}/reviews")
    latest = {review["user"]["login"]: review["state"] for review in reviews}
    if "CHANGES_REQUESTED" in latest.values():
        return False, "outstanding change request"
    fresh = api.get(f"/repos/{full_name}/pulls/{number}")
    if fresh["state"] != "open" or fresh["head"]["sha"] != sha or fresh["base"]["ref"] != repo["default_branch"]:
        return False, "PR changed while reviewing"
    return True, "eligible"


def publish_check(api: GitHub, repo: str, sha: str, *, allowed: bool, reason: str) -> None:
    status = "completed" if allowed or reason != "checks not all complete and successful" else "in_progress"
    conclusion = "success" if allowed else "failure" if status == "completed" else None
    runs = api.get(f"/repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100")["check_runs"]
    existing = next((run for run in runs if run["name"] == CHECK_NAME and run.get("app", {}).get("id") == APP_ID), None)
    if existing and existing["status"] == status and existing.get("conclusion") == conclusion:
        return
    data = {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": status,
        "output": {"title": "Policy reviewer", "summary": reason},
    }
    if conclusion is not None:
        data["conclusion"] = conclusion
    api.request(f"/repos/{repo}/check-runs", method="POST", data=data)


def run(api: GitHub, organization: str, *, dry_run: bool = False) -> int:
    if organization not in ALLOWED_ORGS:
        raise ValueError("organization is not allowlisted")
    passed = 0
    for repo in api.pages("/installation/repositories"):
        if repo["owner"]["login"] != organization or repo["archived"] or repo["disabled"]:
            continue
        full_name = repo["full_name"]
        for pr in api.pages(f"/repos/{full_name}/pulls?state=open"):
            if pr["created_at"] < ROLLOUT_CUTOFF:
                print(f"{full_name}#{pr['number']}: predates reviewer rollout")
                continue
            allowed, reason = eligible(api, repo, pr)
            print(f"{full_name}#{pr['number']}: {reason}")
            fresh = api.get(f"/repos/{full_name}/pulls/{pr['number']}")
            if fresh["state"] != "open" or fresh["head"]["sha"] != pr["head"]["sha"]:
                continue
            if not dry_run:
                if allowed:
                    reviews = api.pages(f"/repos/{full_name}/pulls/{pr['number']}/reviews")
                    latest = {review["user"]["login"]: review["state"] for review in reviews}
                    if latest.get("re8ch-policy-reviewer[bot]") != "APPROVED":
                        api.request(
                            f"/repos/{full_name}/pulls/{pr['number']}/reviews",
                            method="POST",
                            data={"event": "APPROVE", "body": "Policy reviewer: trusted author, verified commits, and completed successful checks."},
                        )
                publish_check(api, full_name, pr["head"]["sha"], allowed=allowed, reason=reason)
            if allowed:
                passed += 1
    return passed


def main() -> None:
    organization = os.environ["REVIEWER_ORGANIZATION"]
    dry_run = os.environ.get("REVIEWER_DRY_RUN", "true").lower() != "false"
    passed = run(GitHub(os.environ["GH_TOKEN"]), organization, dry_run=dry_run)
    print(f"{organization}: {'eligible' if dry_run else 'passing'}={passed}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as exc:
        print(f"reviewer failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
