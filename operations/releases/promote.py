#!/usr/bin/env python3
"""Open an App-authored, GitHub-signed digest-only PR; never bypass CI."""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
import urllib.request
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = "re8ch/service-backend"


def api(path, body=None):
    request = urllib.request.Request("https://api.github.com/" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def graphql(query, variables):
    result = api("graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise RuntimeError("GitHub rejected signed promotion mutation: " + json.dumps(result["errors"]))
    return result["data"]


def changes(directory):
    spec = importlib.util.spec_from_file_location("flux_oci", ROOT / "operations/releases/flux_oci.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    allocator_spec = importlib.util.spec_from_file_location("promotion_names", ROOT / "operations/releases/allocate_codename.py")
    allocator = importlib.util.module_from_spec(allocator_spec)
    allocator_spec.loader.exec_module(allocator)
    additions = []
    current = yaml.safe_load((ROOT / "operations/releases/codenames.yaml").read_text())
    proposed = yaml.safe_load((directory / "codenames.yaml").read_text())
    if {k: v for k, v in current.items() if k != "modules"} != {k: v for k, v in proposed.items() if k != "modules"}:
        raise ValueError("automatic promotion cannot change codename vocabularies")
    updated = set()
    for path in sorted((directory / "releases").glob("*.yaml")):
        release = yaml.safe_load(path.read_text())
        generator.manifests(release) # schema, owner and trusted signer validation
        module = release["module"]
        if release["codename"] != allocator.proposed(module, current):
            raise ValueError("promotion must claim the module's next reviewed codename")
        if path.stem != module.replace("/", "-"):
            raise ValueError("unexpected release filename")
        previous = current["modules"].get(module, {}).get("releases", [])
        next_releases = proposed["modules"][module]["releases"]
        if next_releases[:-1] != previous or len(next_releases) != len(previous) + 1:
            raise ValueError("codename history must be append-only")
        if next_releases[-1] != {"codename": release["codename"], "digest": release["bundle"]["digest"]}:
            raise ValueError("release descriptor and codename history differ")
        source_path = ROOT / "clusters/re8ch/domain-sources" / path.name
        documents = list(yaml.safe_load_all(source_path.read_text()))
        source = next(doc for doc in documents if doc["kind"] == "OCIRepository")
        if source["spec"]["url"] != release["bundle"]["url"]:
            raise ValueError("automatic promotion cannot change registry authority")
        source["spec"]["ref"] = {"digest": release["bundle"]["digest"]}
        source["metadata"]["annotations"]["delivery.re8ch.com/codename"] = release["codename"]
        for document in documents:
            if document["kind"] == "Kustomization":
                document["metadata"]["annotations"]["delivery.re8ch.com/codename"] = release["codename"]
        additions.extend([(str(source_path.relative_to(ROOT)), yaml.safe_dump_all(documents, sort_keys=False)),
                          ("releases/" + path.name, path.read_text())])
        updated.add(module)
    if not updated:
        return []
    untouched = set(current["modules"]) | set(proposed["modules"])
    if any(current["modules"].get(module) != proposed["modules"].get(module) for module in untouched - updated):
        raise ValueError("promotion changed unrelated module history")
    additions.append(("operations/releases/codenames.yaml", (directory / "codenames.yaml").read_text()))
    return additions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    additions = changes(args.directory)
    if not additions:
        print("No active module changes to promote")
        return
    head = api("repos/" + REPO + "/git/ref/heads/main")["object"]["sha"]
    if head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("main advanced since publication; refusing stale automatic promotion")
    branch = "codex/promote-domain-" + os.environ["GITHUB_RUN_ID"]
    api("repos/" + REPO + "/git/refs", {"ref": "refs/heads/" + branch, "sha": head})
    query = "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"
    result = graphql(query, {"input": {"branch": {"repositoryNameWithOwner": REPO, "branchName": branch},
        "expectedHeadOid": head, "message": {"headline": "chore(gitops): promote signed domain digests"},
        "fileChanges": {"additions": [{"path": path, "contents": base64.b64encode(content.encode()).decode()} for path, content in additions]}}})
    commit = result["createCommitOnBranch"]["commit"]["oid"]
    verification = api("repos/" + REPO + "/commits/" + commit)["commit"]["verification"]
    if not verification["verified"]:
        raise RuntimeError("GitHub App commit signature was not verified; refusing promotion")
    pr = api("repos/" + REPO + "/pulls", {"title": "chore(gitops): promote verified domain OCI digests",
        "head": branch, "base": "main", "body": "Signed OCI bundles verified against exact GitHub OIDC publisher identities. This PR changes only active module digest locks and append-only codename history. CI and branch protections remain mandatory."})
    graphql("mutation($input:EnablePullRequestAutoMergeInput!){enablePullRequestAutoMerge(input:$input){pullRequest{number}}}",
            {"input": {"pullRequestId": pr["node_id"], "mergeMethod": "SQUASH"}})
    print("Automatic promotion PR: " + pr["html_url"])


if __name__ == "__main__":
    main()
