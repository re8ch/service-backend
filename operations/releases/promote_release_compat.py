#!/usr/bin/env python3
"""Promote the signed compatibility controller image through a protected PR."""
from __future__ import annotations

import argparse
import base64
import os
import re
from pathlib import Path

from promote_ceph import ROOT, REPO, api, graphql


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"registry\.re8ch\.com/functions-shared/release-compat-controller@sha256:[0-9a-f]{64}", args.image):
        parser.error("immutable compatibility image required")
    path = ROOT / "clusters/re8ch/components/release-compat/controller.yaml"
    body = path.read_text()
    body, count = re.subn(r"registry\.re8ch\.com/functions-shared/release-compat-controller@sha256:[0-9a-f]{64}", args.image, body)
    if count != 2:
        raise RuntimeError("expected projector and dispatcher image slots")
    body = body.replace("  replicas: 0\n  selector: {matchLabels: {delivery.re8ch.com/component: release-compat-controller}}",
                        "  replicas: 2\n  selector: {matchLabels: {delivery.re8ch.com/component: release-compat-controller}}", 1)
    head = api("repos/" + REPO + "/git/ref/heads/main")["object"]["sha"]
    if head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("main advanced; refusing stale controller promotion")
    branch = "codex/promote-release-compat-" + os.environ["GITHUB_RUN_ID"]
    api("repos/" + REPO + "/git/refs", {"ref": "refs/heads/" + branch, "sha": head})
    query = "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"
    result = graphql(query, {"input": {"branch": {"repositoryNameWithOwner": REPO, "branchName": branch},
        "expectedHeadOid": head, "message": {"headline": "chore(flux): activate signed release compatibility controller"},
        "fileChanges": {"additions": [{"path": str(path.relative_to(ROOT)), "contents": base64.b64encode(body.encode()).decode()}]}}})
    commit = result["createCommitOnBranch"]["commit"]["oid"]
    if not api("repos/" + REPO + "/commits/" + commit)["commit"]["verification"]["verified"]:
        raise RuntimeError("promotion commit is not GitHub-verified")
    pr = api("repos/" + REPO + "/pulls", {"title": "chore(flux): activate signed release compatibility controller", "head": branch, "base": "main",
        "body": "Promotes the tested, keyless-signed compatibility image and activates the read/status-only projector. The GitHub App dispatcher remains scaled to zero until its independent credential is provisioned."})
    graphql("mutation($input:EnablePullRequestAutoMergeInput!){enablePullRequestAutoMerge(input:$input){pullRequest{number}}}",
            {"input": {"pullRequestId": pr["node_id"], "mergeMethod": "SQUASH"}})
    print(pr["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
