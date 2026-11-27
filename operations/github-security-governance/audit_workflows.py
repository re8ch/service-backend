#!/usr/bin/env python3
"""Read-only inventory of GitHub Actions workflow references."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.parse
from collections import Counter
from pathlib import Path

from audit import Client


USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def classify(reference: str) -> str:
    if reference.startswith("./"):
        return "local"
    if reference.startswith("docker://"):
        return "docker"
    if reference.startswith("${{"):
        return "dynamic"
    if "@" not in reference:
        return "unversioned"
    _, revision = reference.rsplit("@", 1)
    return "immutable" if SHA.fullmatch(revision) else "mutable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enterprise-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=0.1)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GH_TOKEN is required")
    client = Client(token, args.pause)
    repositories = json.loads(args.enterprise_audit.read_text())["repositories"]

    records = []
    for repository in repositories:
        if repository["archived"]:
            continue
        name = repository["repository"]
        branch = urllib.parse.quote(repository["defaultBranch"], safe="")
        status, listing = client.get(
            f"/repos/{name}/contents/.github/workflows?ref={branch}"
        )
        if status == 404:
            records.append({"repository": name, "status": status, "workflows": []})
            continue
        if status != 200 or not isinstance(listing, list):
            records.append(
                {"repository": name, "status": status, "error": "workflow listing"}
            )
            continue
        workflows = []
        for item in listing:
            if item.get("type") != "file" or not item["name"].endswith((".yml", ".yaml")):
                continue
            path = urllib.parse.quote(item["path"], safe="/")
            file_status, content = client.get(
                f"/repos/{name}/contents/{path}?ref={branch}"
            )
            if file_status != 200 or not isinstance(content, dict):
                workflows.append(
                    {"path": item["path"], "status": file_status, "references": []}
                )
                continue
            text = base64.b64decode(content["content"]).decode(errors="replace")
            references = [
                {"reference": reference, "classification": classify(reference)}
                for reference in USES.findall(text)
            ]
            workflows.append(
                {"path": item["path"], "status": file_status, "references": references}
            )
        records.append({"repository": name, "status": status, "workflows": workflows})

    counts = Counter(
        reference["classification"]
        for repository in records
        for workflow in repository.get("workflows", [])
        for reference in workflow["references"]
    )
    result = {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "GitHubActionsReferenceAudit",
        "readOnly": True,
        "summary": {
            "repositories": len(records),
            "repositoriesWithWorkflows": sum(bool(item.get("workflows")) for item in records),
            "workflows": sum(len(item.get("workflows", [])) for item in records),
            "references": sum(counts.values()),
            "classifications": dict(sorted(counts.items())),
        },
        "repositories": records,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
