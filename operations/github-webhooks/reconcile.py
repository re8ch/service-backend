#!/usr/bin/env python3
"""Plan/apply one GitHub organization webhook and audit repository exceptions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
DEFAULT_DECLARATION = ROOT / "operations/github-webhooks/desired.yaml"
API_VERSION = "2026-03-10"


def gh(method: str, endpoint: str, payload: dict | None = None) -> object:
    command = ["gh", "api", "--header", f"X-GitHub-Api-Version: {API_VERSION}"]
    if method != "GET":
        command.extend(["--method", method])
    command.append(endpoint)
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(
        command,
        input=None if payload is None else json.dumps(payload),
        text=True,
        check=True,
        capture_output=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def desired(declaration: dict, environ: dict[str, str]) -> dict:
    spec = declaration["spec"]["organization"]
    url = environ.get(spec["urlEnvironment"], "").strip()
    return {
        "login": spec["login"],
        "active": bool(spec.get("active", True)),
        "events": sorted(set(spec["events"])),
        "url": url,
        "secretEnvironment": spec["secretEnvironment"],
    }


def plan_hook(target: dict, hooks: list[dict]) -> dict:
    matches = [item for item in hooks if item.get("config", {}).get("url") == target["url"]]
    if not target["url"]:
        return {"action": "blocked", "reason": "webhook URL environment is unset"}
    if not matches:
        return {"action": "create", "events": target["events"], "active": target["active"]}
    hook = matches[0]
    drift = {}
    if sorted(hook.get("events", [])) != target["events"]:
        drift["events"] = target["events"]
    if bool(hook.get("active")) != target["active"]:
        drift["active"] = target["active"]
    return {"action": "update" if drift else "none", "hookId": hook["id"], "changes": drift}


def apply_hook(target: dict, plan: dict, environ: dict[str, str]) -> None:
    endpoint = f"/orgs/{target['login']}/hooks"
    if plan["action"] == "create":
        secret = environ.get(target["secretEnvironment"], "")
        if not secret:
            raise SystemExit("webhook secret environment is unset")
        gh("POST", endpoint, {
            "name": "web",
            "active": target["active"],
            "events": target["events"],
            "config": {
                "url": target["url"],
                "content_type": "json",
                "insecure_ssl": "0",
                "secret": secret,
            },
        })
    elif plan["action"] == "update":
        gh("PATCH", f"{endpoint}/{plan['hookId']}", plan["changes"])


def exception_observations(declaration: dict) -> list[dict]:
    observations = []
    for item in declaration["spec"].get("repositoryExceptions", []):
        owner, repo = item["repository"].split("/", 1)
        hooks = gh("GET", f"/repos/{owner}/{repo}/hooks")
        observations.append({
            "repository": item["repository"],
            "policy": item["policy"],
            "hooks": [{
                "id": hook["id"],
                "active": hook["active"],
                "events": hook["events"],
                "url": hook.get("config", {}).get("url"),
                "lastResponse": hook.get("last_response"),
            } for hook in hooks],
        })
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--declaration", type=Path, default=DEFAULT_DECLARATION)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    declaration = yaml.safe_load(args.declaration.read_text())
    target = desired(declaration, os.environ)
    plan = plan_hook(target, [])
    if plan["action"] != "blocked":
        org_hooks = gh("GET", f"/orgs/{target['login']}/hooks")
        plan = plan_hook(target, org_hooks)
    if args.apply:
        if plan["action"] == "blocked":
            raise SystemExit(plan["reason"])
        apply_hook(target, plan, os.environ)
        org_hooks = gh("GET", f"/orgs/{target['login']}/hooks")
        plan = plan_hook(target, org_hooks)
        if plan["action"] != "none":
            raise SystemExit(f"effective organization webhook still differs: {plan}")
    print(json.dumps({
        "organization": target["login"],
        "plan": plan,
        "repositoryExceptions": exception_observations(declaration),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
