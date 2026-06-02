#!/usr/bin/env python3
"""Prune tenant Harbor projects to the images currently used by the cluster."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageRef:
    project: str
    repo: str
    tag: str


def credentials(registry: str) -> tuple[str, str]:
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


def request(method: str, url: str, user: str, password: str) -> object:
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
        batch = request("GET", f"{url}{sep}page={page}&page_size=100", user, password)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def parse_image(line: str, registry: str) -> ImageRef | None:
    image = line.strip()
    if not image or not image.startswith(f"{registry}/") or ":" not in image:
        return None
    path, tag = image[len(registry) + 1 :].rsplit(":", 1)
    project, repo = path.split("/", 1)
    return ImageRef(project=project, repo=repo, tag=tag)


def load_keep(path: str, registry: str) -> set[ImageRef]:
    keep: set[ImageRef] = set()
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            ref = parse_image(line, registry)
            if ref:
                keep.add(ref)
    return keep


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune Harbor tenant project images not present in a keep list.")
    parser.add_argument("--registry", default=os.environ.get("REGISTRY_HOST", "registry.re8ch.com"))
    parser.add_argument("--keep-file", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("projects", nargs="*", default=["qianwen", "ledger", "anycam", "bench", "functions-shared"])
    args = parser.parse_args()

    user, password = credentials(args.registry)
    base = f"https://{args.registry}/api/v2.0"
    keep = load_keep(args.keep_file, args.registry)

    delete_artifacts = 0
    keep_artifacts = 0
    delete_tags = 0
    keep_tags = 0
    for project in args.projects:
        repos = paged(f"{base}/projects/{quote(project)}/repositories", user, password)
        for repo in repos:
            name = str(repo.get("name", ""))
            prefix = f"{project}/"
            repo_path = name[len(prefix) :] if name.startswith(prefix) else name
            artifacts = paged(
                f"{base}/projects/{quote(project)}/repositories/{quote(repo_path)}/artifacts",
                user,
                password,
            )
            for artifact in artifacts:
                digest = str(artifact.get("digest", ""))
                tags = [tag.get("name") for tag in artifact.get("tags") or [] if tag.get("name")]
                kept = [tag for tag in tags if ImageRef(project, repo_path, tag) in keep]
                removed = [tag for tag in tags if ImageRef(project, repo_path, tag) not in keep]
                if kept:
                    keep_artifacts += 1
                    keep_tags += len(kept)
                    for tag in removed:
                        delete_tags += 1
                        url = (
                            f"{base}/projects/{quote(project)}/repositories/{quote(repo_path)}"
                            f"/artifacts/{quote(digest)}/tags/{quote(tag)}"
                        )
                        print(f"delete-tag {project}/{repo_path}:{tag}")
                        if args.execute:
                            request("DELETE", url, user, password)
                    continue

                delete_artifacts += 1
                print(f"delete-artifact {project}/{repo_path}@{digest} tags={','.join(tags) or '-'}")
                if args.execute:
                    url = f"{base}/projects/{quote(project)}/repositories/{quote(repo_path)}/artifacts/{quote(digest)}"
                    request("DELETE", url, user, password)

    print(
        "summary "
        f"execute={args.execute} keep_artifacts={keep_artifacts} keep_tags={keep_tags} "
        f"delete_artifacts={delete_artifacts} delete_tags={delete_tags}"
    )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
