#!/usr/bin/env python3
"""Fail-closed, human-dispatched merge executed by the Maintenance App."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from typing import Any


TRUSTED_ACTOR = "Woomou"
TRUSTED_REPOSITORY = "re8ch/service-backend"
TRUSTED_REF = "refs/heads/main"


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise ValueError("Maintenance App installation token is required")
        self.token = token

    def request(self, path: str, *, method: str = "GET", data: dict[str, Any] | None = None) -> Any:
        command = ["gh", "api", path, "--method", method, "-H", "Accept: application/vnd.github+json"]
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
                return json.loads(result.stdout)
            if attempt == attempts - 1:
                raise RuntimeError(f"GitHub API request failed for {path}: {result.stderr.strip()[:300]}")
            time.sleep(attempt + 1)
        raise AssertionError("unreachable")

    def pages(self, path: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for page in range(1, 101):
            join = "&" if "?" in path else "?"
            batch = self.request(f"{path}{join}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError(f"expected a list from {path}")
            values.extend(batch)
            if len(batch) < 100:
                return values
        raise ValueError(f"pagination limit exceeded for {path}")


def validate_invocation() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository != TRUSTED_REPOSITORY:
        raise ValueError("merge workflow is restricted to re8ch/service-backend")
    if os.environ.get("GITHUB_ACTOR") != TRUSTED_ACTOR:
        raise ValueError("merge workflow must be dispatched by Woomou")
    if os.environ.get("GITHUB_REF") != TRUSTED_REF:
        raise ValueError("merge workflow must run from main")
    return repository


def merge(api: GitHub, repository: str, number: int, expected_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("head SHA must be a full lowercase commit SHA")
    pr = api.request(f"/repos/{repository}/pulls/{number}")
    if pr["state"] != "open" or pr["draft"]:
        raise ValueError("pull request must be open and ready for review")
    if pr["base"]["ref"] != "main":
        raise ValueError("pull request must target main")
    if pr["head"]["repo"] is None or pr["head"]["repo"]["full_name"] != repository:
        raise ValueError("fork pull requests are not eligible")
    if pr["user"]["login"] != TRUSTED_ACTOR:
        raise ValueError("pull request author is not trusted")
    if pr["head"]["sha"] != expected_sha:
        raise ValueError("pull request head changed after human authorization")

    commits = api.pages(f"/repos/{repository}/pulls/{number}/commits")
    if not commits or any(
        not commit["commit"]["verification"]["verified"]
        or commit.get("author") is None
        or commit["author"]["login"] != TRUSTED_ACTOR
        for commit in commits
    ):
        raise ValueError("all commits must be verified and attributed to Woomou")

    reviews = api.pages(f"/repos/{repository}/pulls/{number}/reviews")
    latest_reviews = {review["user"]["login"]: review["state"] for review in reviews}
    if "CHANGES_REQUESTED" in latest_reviews.values():
        raise ValueError("pull request has an outstanding change request")

    # GitHub evaluates the current main ruleset atomically here: required
    # checks, strict up-to-date status, signatures, linear history and resolved
    # conversations cannot be bypassed by this App installation.
    result = api.request(
        f"/repos/{repository}/pulls/{number}/merge",
        method="PUT",
        data={
            "sha": expected_sha,
            "merge_method": "squash",
            "commit_title": f"{pr['title']} (#{number})",
        },
    )
    if not result.get("merged"):
        raise RuntimeError(f"GitHub refused protected merge: {result.get('message', 'unknown reason')}")
    print(f"Maintenance App merged {repository}#{number} at authorized head {expected_sha}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pull_request", type=int)
    parser.add_argument("head_sha")
    args = parser.parse_args()
    repository = validate_invocation()
    merge(GitHub(os.environ.get("GH_TOKEN", "")), repository, args.pull_request, args.head_sha)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"bot merge refused: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from None
