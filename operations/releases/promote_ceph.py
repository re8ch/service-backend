#!/usr/bin/env python3
"""Open an App-authored, GitHub-signed PR for one verified Ceph bundle."""
from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "operations/releases"))
REPO = "re8ch/service-backend"


def api(path: str, body=None):
    request = urllib.request.Request("https://api.github.com/" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def graphql(query: str, variables: dict):
    result = api("graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise RuntimeError(json.dumps(result["errors"]))
    return result["data"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--chart-digest", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.digest):
        parser.error("digest must be immutable")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.chart_digest):
        parser.error("chart digest must be immutable")
    lock_path = ROOT / "contracts/releases/ceph/chart-locks.yaml"
    lock = yaml.safe_load(lock_path.read_text())
    entry = next((item for item in lock["charts"] if item["name"] == args.release), None)
    if not entry:
        parser.error("unknown release")
    if entry.get("bundleDigest") == args.digest and entry.get("mirrorDigest") == args.chart_digest:
        print("chart and bundle already promoted")
        return 0
    entry["bundleDigest"] = args.digest
    entry["mirrorDigest"] = args.chart_digest
    lock_body = yaml.safe_dump(lock, sort_keys=False)
    compiler_spec = importlib.util.spec_from_file_location("compile_ceph_static", ROOT / "operations/releases/compile_ceph_static.py")
    compiler = importlib.util.module_from_spec(compiler_spec)
    compiler_spec.loader.exec_module(compiler)
    original = compiler.LOCK
    with tempfile.TemporaryDirectory(prefix="ceph-promote-") as temporary:
        proposed = Path(temporary) / "chart-locks.yaml"
        proposed.write_text(lock_body)
        compiler.LOCK = proposed
        generated = compiler.outputs()
    compiler.LOCK = original
    additions = [{"path": str(lock_path.relative_to(ROOT)), "contents": base64.b64encode(lock_body.encode()).decode()}]
    for path, body in generated.items():
        if path == lock_path:
            continue
        current = path.read_text() if path.exists() else ""
        if current != body:
            additions.append({"path": str(path.relative_to(ROOT)), "contents": base64.b64encode(body.encode()).decode()})
    head = api("repos/" + REPO + "/git/ref/heads/main")["object"]["sha"]
    if head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("main advanced; refusing stale Ceph promotion")
    branch = f"codex/promote-ceph-{args.release}-{os.environ['GITHUB_RUN_ID']}"
    api("repos/" + REPO + "/git/refs", {"ref": "refs/heads/" + branch, "sha": head})
    query = "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"
    result = graphql(query, {"input": {"branch": {"repositoryNameWithOwner": REPO, "branchName": branch},
        "expectedHeadOid": head, "message": {"headline": f"chore(ceph): promote signed {args.release} bundle"},
        "fileChanges": {"additions": additions}}})
    commit = result["createCommitOnBranch"]["commit"]["oid"]
    if not api("repos/" + REPO + "/commits/" + commit)["commit"]["verification"]["verified"]:
        raise RuntimeError("promotion commit is not GitHub-verified")
    pr = api("repos/" + REPO + "/pulls", {"title": f"chore(ceph): promote signed {args.release} bundle",
        "head": branch, "base": "main", "body": "Promotes one digest-addressed, keyless-signed static Ceph bundle. CI regenerates the exact chart inventory; branch protection remains mandatory."})
    graphql("mutation($input:EnablePullRequestAutoMergeInput!){enablePullRequestAutoMerge(input:$input){pullRequest{number}}}",
            {"input": {"pullRequestId": pr["node_id"], "mergeMethod": "SQUASH"}})
    print(pr["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
