#!/usr/bin/env python3
"""Copy Harbor images from the legacy nuclio project into tenant projects.

The script uses Harbor's server-side artifact copy API. It does not delete
legacy images and does not pull images through the local Docker daemon.
Credentials are read from env or the local Docker credential helper.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass(frozen=True)
class CopyItem:
    source_project: str
    source_repo_path: str
    source_repo: str
    source_tag: str
    target_project: str
    target_repo: str

    @property
    def source_image(self) -> str:
        return f"{self.source_repo}:{self.source_tag}"

    @property
    def target_image(self) -> str:
        return f"{self.target_project}/{self.target_repo}:{self.source_tag}"


def docker_credentials(registry: str) -> tuple[str, str]:
    user = os.environ.get("HARBOR_USERNAME")
    password = os.environ.get("HARBOR_PASSWORD")
    if user and password:
        return user, password

    helper = os.environ.get("DOCKER_CREDENTIAL_HELPER", "docker-credential-osxkeychain")
    try:
        raw = subprocess.check_output([helper, "get"], input=registry.encode(), stderr=subprocess.DEVNULL)
        data = json.loads(raw)
        return data["Username"], data["Secret"]
    except Exception as exc:  # pragma: no cover - local credential fallback
        raise SystemExit(f"Cannot read Harbor credentials from env or {helper}: {exc}") from exc


def harbor_get(url: str, user: str, password: str) -> object:
    return harbor_request("GET", url, user, password)


def harbor_request(method: str, url: str, user: str, password: str) -> object:
    req = urllib.request.Request(url)
    req.method = method
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=120) as res:
        raw = res.read()
        if not raw:
            return {}
        return json.loads(raw)


def paged(url: str, user: str, password: str) -> list[object]:
    items: list[object] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        batch = harbor_get(f"{url}{sep}page={page}&page_size=100", user, password)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def target_for_repo(repo_path: str, include_platform: bool) -> tuple[str, str] | None:
    leaf = repo_path.rsplit("/", 1)[-1]
    probe = repo_path
    if "processor-qianwen-" in probe:
        return "qianwen", leaf if leaf.startswith("processor-") else probe.split("processor-", 1)[1]
    if "processor-ledger-" in probe:
        return "ledger", leaf if leaf.startswith("processor-") else "processor-" + probe.split("processor-", 1)[1]
    if "processor-anycam-" in probe:
        return "anycam", leaf if leaf.startswith("processor-") else "processor-" + probe.split("processor-", 1)[1]
    if "processor-bench-" in probe:
        return "bench", leaf if leaf.startswith("processor-") else "processor-" + probe.split("processor-", 1)[1]
    if include_platform:
        return "functions-shared", leaf
    return None


def copy_artifact(base: str, item: CopyItem, user: str, password: str, dry_run: bool) -> str:
    source_ref = f"{item.source_project}/{item.source_repo_path}:{item.source_tag}"
    target_project = urllib.parse.quote(item.target_project, safe="")
    target_repo = urllib.parse.quote(item.target_repo, safe="")
    source_query = urllib.parse.quote(source_ref, safe="")
    url = f"{base}/projects/{target_project}/repositories/{target_repo}/artifacts?from={source_query}"
    print(f"{source_ref} -> {item.target_project}/{item.target_repo}:{item.source_tag}", flush=True)
    if dry_run:
        return "dry-run"
    attempts = int(os.environ.get("COPY_IMAGE_RETRIES", "3"))
    for attempt in range(1, attempts + 1):
        try:
            harbor_request("POST", url, user, password)
            return "copied"
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return "exists"
            if attempt == attempts:
                raise
            print(f"retry {attempt}/{attempts}: HTTP {exc.code} {source_ref}", file=sys.stderr, flush=True)
    return "failed"


def parse_target(target: tuple[str, str]) -> tuple[str, str]:
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy legacy Harbor Nuclio images into tenant projects.")
    parser.add_argument("--registry", default=os.environ.get("REGISTRY_HOST", "registry.re8ch.com"))
    parser.add_argument("--source-project", default=os.environ.get("SOURCE_PROJECT", "nuclio"))
    parser.add_argument("--include-platform", action="store_true", help="also copy non-processor images to functions-shared")
    parser.add_argument("--execute", action="store_true", help="perform Harbor server-side artifact copy")
    parser.add_argument("--repo", action="append", default=[], help="only copy this repository path under the source project")
    parser.add_argument("--tag", action="append", default=[], help="only copy this tag")
    parser.add_argument("--skip-existing", action="store_true", help="accepted for compatibility; existing targets are skipped automatically")
    args = parser.parse_args()

    user, password = docker_credentials(args.registry)
    base = f"https://{args.registry}/api/v2.0"
    repos = paged(f"{base}/projects/{urllib.parse.quote(args.source_project, safe='')}/repositories", user, password)

    wanted_repos = set(args.repo)
    wanted_tags = set(args.tag)
    plan: list[CopyItem] = []
    for repo in repos:
        name = str(repo.get("name", ""))
        prefix = f"{args.source_project}/"
        repo_path = name[len(prefix):] if name.startswith(prefix) else name
        if wanted_repos and repo_path not in wanted_repos and name not in wanted_repos:
            continue
        target = target_for_repo(repo_path, args.include_platform)
        if not target:
            continue
        target_project, target_repo = parse_target(target)
        encoded = urllib.parse.quote(repo_path, safe="")
        try:
            artifacts = paged(
                f"{base}/projects/{urllib.parse.quote(args.source_project, safe='')}/repositories/{encoded}/artifacts",
                user,
                password,
            )
        except urllib.error.HTTPError as exc:
            print(f"skip-api-error {exc.code} {name}", file=sys.stderr, flush=True)
            continue
        for artifact in artifacts:
            for tag in artifact.get("tags") or []:
                tag_name = tag.get("name")
                if not tag_name or (wanted_tags and tag_name not in wanted_tags):
                    continue
                plan.append(
                    CopyItem(
                        source_repo=f"{args.registry}/{args.source_project}/{repo_path}",
                        source_project=args.source_project,
                        source_repo_path=repo_path,
                        source_tag=tag_name,
                        target_project=target_project,
                        target_repo=target_repo,
                    )
                )

    print(f"copy plan: {len(plan)} tags", flush=True)
    counts = {"copied": 0, "exists": 0, "dry-run": 0, "failed": 0}
    for item in plan:
        result = copy_artifact(base, item, user, password, dry_run=not args.execute)
        counts[result] = counts.get(result, 0) + 1
    print("summary " + " ".join(f"{key}={value}" for key, value in sorted(counts.items())), flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
