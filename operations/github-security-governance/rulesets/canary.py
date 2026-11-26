#!/usr/bin/env python3
"""Validate, apply, and read back evaluate-only repository Ruleset canaries."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


API = "https://api.github.com"
VERSION = "2022-11-28"
ALLOWED_RULES = {
    "deletion",
    "non_fast_forward",
    "required_linear_history",
    "required_signatures",
    "pull_request",
    "required_status_checks",
}


def request(token: str, method: str, path: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": VERSION,
            "User-Agent": "re8ch-ruleset-canary",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, json.loads(body) if body else None


def validate(data: dict) -> list[str]:
    errors = []
    repositories = set()
    for canary in data.get("canaries", []):
        repository = canary.get("repository")
        ruleset = canary.get("ruleset", {})
        if not repository or repository in repositories:
            errors.append("canary repository identities must be present and unique")
        repositories.add(repository)
        if ruleset.get("enforcement") != "evaluate":
            errors.append(f"{repository}: canary enforcement must be evaluate")
        if ruleset.get("bypass_actors"):
            errors.append(f"{repository}: canary bypass actors must be empty")
        types = {rule.get("type") for rule in ruleset.get("rules", [])}
        if not types <= ALLOWED_RULES:
            errors.append(f"{repository}: unsupported rule type")
        checks = [
            check.get("context")
            for rule in ruleset.get("rules", [])
            if rule.get("type") == "required_status_checks"
            for check in rule.get("parameters", {}).get("required_status_checks", [])
        ]
        if checks != ["render"] or not canary.get("requiredCheckEvidence"):
            errors.append(f"{repository}: render needs immutable PR evidence")
    return errors


def status(token: str, canary: dict) -> dict:
    repository = canary["repository"]
    expected = canary["ruleset"]
    code, values = request(token, "GET", f"/repos/{repository}/rulesets")
    matches = [] if code != 200 else [item for item in values if item["name"] == expected["name"]]
    return {"repository": repository, "httpStatus": code, "matches": matches}


def apply(token: str, canary: dict) -> dict:
    current = status(token, canary)
    if len(current["matches"]) > 1:
        raise RuntimeError(f"duplicate canary Rulesets for {canary['repository']}")
    if current["matches"]:
        identifier = current["matches"][0]["id"]
        code, value = request(
            token,
            "PUT",
            f"/repos/{canary['repository']}/rulesets/{identifier}",
            canary["ruleset"],
        )
    else:
        code, value = request(
            token,
            "POST",
            f"/repos/{canary['repository']}/rulesets",
            canary["ruleset"],
        )
    if code not in {200, 201}:
        raise RuntimeError(f"Ruleset apply failed for {canary['repository']}: {code} {value}")
    readback = status(token, canary)
    match = readback["matches"]
    if len(match) != 1 or match[0].get("enforcement") != "evaluate":
        raise RuntimeError(f"Ruleset readback failed for {canary['repository']}: {readback}")
    return {"repository": canary["repository"], "rulesetId": match[0]["id"], "enforcement": "evaluate"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--declaration", type=Path, default=Path(__file__).with_name("canaries.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.declaration.read_text())
    errors = validate(data)
    for error in errors:
        print(error)
    if errors:
        return 1
    if not args.apply:
        print(json.dumps({"valid": True, "canaries": len(data["canaries"])}))
        return 0
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GH_TOKEN is required for --apply")
    print(json.dumps([apply(token, canary) for canary in data["canaries"]], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
