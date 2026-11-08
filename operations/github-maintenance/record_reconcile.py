#!/usr/bin/env python3
"""Route healthy reconcile evidence to Wiki and incidents to Issue-backed Projects."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


INCIDENT_STATES = frozenset({"Failed", "Degraded", "Unknown"})


def route(state: str) -> str:
    if state == "Healthy":
        return "wiki"
    if state in INCIDENT_STATES:
        return "incident"
    raise ValueError(f"unsupported observation state: {state}")


def run(args: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    env["GIT_CONFIG_VALUE_0"] = "!f() { echo username=x-access-token; echo password=$GH_TOKEN; }; f"
    return env


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, env=git_env())


def record_wiki(repository: str, source: str, dedupe: str, body: str) -> None:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    page = f"GitOps-Reconcile-Journal-{month}.md"
    marker = f"<!-- reconcile:{dedupe} -->"
    with tempfile.TemporaryDirectory(prefix="reconcile-wiki-") as tmp:
        checkout = Path(tmp) / "wiki"
        git(["clone", "--quiet", f"https://github.com/{repository}.wiki.git", str(checkout)], Path(tmp))
        target = checkout / page
        current = target.read_text() if target.exists() else f"# GitOps Reconcile Journal · {month}\n\n"
        if marker in current:
            return
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target.write_text(current + f"## {timestamp} · {source}\n\n{marker}\n\n{body.rstrip()}\n\n")
        git(["add", page], checkout)
        git(["-c", "user.name=re8ch-gitops-maintenance[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "--quiet", "-m", f"journal: record healthy reconcile for {source}"], checkout)
        for attempt in range(3):
            try:
                git(["push", "--quiet", "origin", "HEAD"], checkout)
                return
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                git(["pull", "--rebase", "--quiet", "origin", "HEAD"], checkout)


def gh_json(args: list[str]) -> str:
    return run(["gh", *args], capture=True)


def incident_label(source: str) -> str:
    encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip("=")
    return f"gitops-reconcile-{encoded}"


def upsert_incident(repository: str, project_owner: str, project_number: int, source: str, state: str, body: str) -> None:
    label = incident_label(source)
    run(["gh", "label", "create", label, "--repo", repository, "--color", "B60205", "--description", f"Open reconcile incident for {source}", "--force"])
    issue = gh_json(["issue", "list", "--repo", repository, "--state", "open", "--label", label, "--limit", "1", "--json", "number,url", "--jq", ".[0] // empty"])
    title = f"GitOps reconcile {state.lower()} · {source}"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as stream:
        stream.write(body)
        body_path = stream.name
    try:
        if issue:
            value = json.loads(issue)
            run(["gh", "issue", "edit", str(value["number"]), "--repo", repository, "--title", title, "--body-file", body_path])
            url = value["url"]
        else:
            url = gh_json(["issue", "create", "--repo", repository, "--title", title, "--body-file", body_path, "--label", label, "--label", "type/maintenance", "--label", "actor/automation"])
        run(["gh", "project", "item-add", str(project_number), "--owner", project_owner, "--url", url])
    finally:
        Path(body_path).unlink(missing_ok=True)


def close_incident(repository: str, source: str) -> None:
    label = incident_label(source)
    number = gh_json(["issue", "list", "--repo", repository, "--state", "open", "--label", label, "--limit", "1", "--json", "number", "--jq", ".[0].number // empty"])
    if number:
        run(["gh", "issue", "close", number, "--repo", repository, "--comment", "A subsequent reconcile produced healthy runtime evidence. The success receipt is recorded in the Wiki journal."])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--dedupe-key", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--wiki-repository", required=True)
    parser.add_argument("--incident-repository", required=True)
    parser.add_argument("--project-owner", required=True)
    parser.add_argument("--project-number", required=True, type=int)
    args = parser.parse_args()
    body = args.body_file.read_text()
    if route(args.state) == "wiki":
        record_wiki(args.wiki_repository, args.source_repository, args.dedupe_key, body)
        close_incident(args.incident_repository, args.source_repository)
    else:
        upsert_incident(args.incident_repository, args.project_owner, args.project_number, args.source_repository, args.state, body)


if __name__ == "__main__":
    main()
