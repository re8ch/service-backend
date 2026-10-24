#!/usr/bin/env python3
"""Promote one verified tenant MCP image through a protected GitOps PR."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "operations/releases"))
from promote_ceph import api, graphql  # noqa: E402


REPO = "re8ch/service-backend"
IMAGE_PATTERN = re.compile(
    r"registry\.re8ch\.com/image/re8ch-tenant-mcp@sha256:[0-9a-f]{64}"
)
SOURCE = Path("platform/birfucate-metering/release.yaml")
BUNDLE = Path("clusters/re8ch/domains/observability-metering/resources.yaml")
INVENTORY = Path("operations/releases/helmrelease-inventory.yaml")
RELEASE_ID = "helmrelease:flux-system:birfucate-metering"


def digest(value) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()


def replace_image(body: str, current: str, proposed: str, path: Path) -> str:
    if body.count(current) != 1:
        raise RuntimeError(f"expected one current tenant MCP image in {path}")
    unexpected = set(IMAGE_PATTERN.findall(body)) - {current}
    if unexpected:
        raise RuntimeError(f"multiple tenant MCP image authorities in {path}")
    return body.replace(current, proposed)


def changes(image: str, root: Path = ROOT) -> list[tuple[str, str]]:
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("immutable tenant MCP image required")
    source_path = root / SOURCE
    source_body = source_path.read_text()
    current_images = set(IMAGE_PATTERN.findall(source_body))
    if len(current_images) != 1:
        raise RuntimeError("tenant MCP source must declare exactly one immutable image")
    current = current_images.pop()
    if current == image:
        return []

    source_body = replace_image(source_body, current, image, SOURCE)
    bundle_body = replace_image((root / BUNDLE).read_text(), current, image, BUNDLE)
    release = next(
        document
        for document in yaml.safe_load_all(source_body)
        if isinstance(document, dict) and document.get("kind") == "HelmRelease"
    )

    inventory = yaml.safe_load((root / INVENTORY).read_text())
    record = next(
        (item for item in inventory["releases"] if item.get("id") == RELEASE_ID),
        None,
    )
    if record is None:
        raise RuntimeError("tenant MCP HelmRelease is absent from the checked inventory")
    record["values"]["inlineSha256"] = digest(release["spec"]["values"])
    record["declarationSha256"] = digest(release)
    inventory_body = yaml.safe_dump(inventory, sort_keys=False)
    return [
        (str(SOURCE), source_body),
        (str(BUNDLE), bundle_body),
        (str(INVENTORY), inventory_body),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        additions = changes(args.image)
    except ValueError as error:
        parser.error(str(error))
    if not additions:
        print("tenant MCP image already promoted")
        return 0

    head = api("repos/" + REPO + "/git/ref/heads/main")["object"]["sha"]
    if head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("main advanced; refusing stale tenant MCP promotion")
    branch = "codex/promote-tenant-mcp-" + os.environ["GITHUB_RUN_ID"]
    api("repos/" + REPO + "/git/refs", {"ref": "refs/heads/" + branch, "sha": head})
    query = "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"
    result = graphql(query, {"input": {
        "branch": {"repositoryNameWithOwner": REPO, "branchName": branch},
        "expectedHeadOid": head,
        "message": {"headline": "chore(gitops): promote verified tenant MCP image"},
        "fileChanges": {"additions": [
            {"path": path, "contents": base64.b64encode(content.encode()).decode()}
            for path, content in additions
        ]},
    }})
    commit = result["createCommitOnBranch"]["commit"]["oid"]
    if not api("repos/" + REPO + "/commits/" + commit)["commit"]["verification"]["verified"]:
        raise RuntimeError("tenant MCP promotion commit is not GitHub-verified")
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', REPO)}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    pr = api("repos/" + REPO + "/pulls", {
        "title": "chore(gitops): promote verified tenant MCP image",
        "head": branch,
        "base": "main",
        "body": (
            "Promotes the digest-pinned tenant MCP image after build, SBOM generation, "
            f"keyless signing, and verification in [{os.environ['GITHUB_RUN_ID']}]({run_url}).\n\n"
            f"Image: `{args.image}`\n\n"
            "The source declaration, compiled static domain bundle, and HelmRelease inventory "
            "are updated atomically. CI and branch protection remain mandatory."
        ),
    })
    graphql(
        "mutation($input:EnablePullRequestAutoMergeInput!){enablePullRequestAutoMerge(input:$input){pullRequest{number}}}",
        {"input": {"pullRequestId": pr["node_id"], "mergeMethod": "SQUASH"}},
    )
    print("Automatic tenant MCP promotion PR: " + pr["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
