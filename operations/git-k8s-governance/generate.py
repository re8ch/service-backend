#!/usr/bin/env python3
"""Generate project-local governance metadata and catalog-owned K8s identities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


CONTROL_PLANE_REPOSITORY = "git@gitlab.com:reachievellc/service-backend.git"


def load_modules(root: Path) -> list[tuple[Path, dict]]:
    return [
        (path, yaml.safe_load(path.read_text()))
        for path in sorted(root.glob("catalogs/*/modules/*/module.yaml"))
    ]


def project_binding(path: Path, document: dict) -> dict:
    metadata, spec = document["metadata"], document["spec"]
    name, catalog = metadata["name"], metadata["catalog"]
    identity = spec["identity"]
    root_source = spec["source"]["kind"] == "root"
    return {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "ProjectBinding",
        "metadata": {"name": name},
        "spec": {
            "parent": {
                "catalog": catalog,
                "repository": CONTROL_PLANE_REPOSITORY,
                "catalogPath": str(path.parent.relative_to(path.parents[4])),
                "submodulePath": "." if root_source else str(path.parent.relative_to(path.parents[4]) / "source"),
            },
            "identity": {
                "maintainerAddress": identity["maintainerAddress"],
                "accountClass": identity["accountClass"],
                "googleWorkspacePolicy": "eligible" if spec["ownership"] == "owned" else "external",
            },
            "workspace": {
                "name": identity["maintainerAddress"].split("@", 1)[0],
                "oneActivePerAccount": True,
                "branchPrefix": f"workspace/{identity['maintainerAddress'].split('@', 1)[0]}/",
            },
            "contract": {
                "transport": "branch-pull-request",
                "completion": "pull-request-closed",
                "evidenceAuthority": "kubernetes",
            },
        },
    }


def cluster_resource_index(document: dict) -> dict:
    metadata, spec = document["metadata"], document["spec"]
    return {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "ClusterResourceIndex",
        "metadata": {"name": metadata["name"]},
        "spec": {
            "controlPlane": {
                "repository": CONTROL_PLANE_REPOSITORY,
                "revision": "main",
                "indexPath": "indexes/deployments.json",
            },
            "lookup": {
                "module": metadata["name"],
                "catalog": metadata["catalog"],
                "declaredDeployments": spec.get("deployments", []),
            },
            "evidence": {
                "authority": "kubernetes",
                "requiredPlanes": ["monitoring", "deploying", "planning"],
            },
        },
    }


def k8s_resources(document: dict) -> list[dict]:
    metadata, spec = document["metadata"], document["spec"]
    if spec["ownership"] != "owned":
        return []
    name = metadata["name"]
    identity = spec["identity"]
    local_part = identity["maintainerAddress"].split("@", 1)[0]
    return [
        {
            "apiVersion": "governance.re8ch.com/v1alpha1",
            "kind": "GitAccount",
            "metadata": {"name": local_part, "namespace": "git-workspaces"},
            "spec": {
                "email": identity["maintainerAddress"],
                "provider": spec["repository"]["provider"],
                "project": name,
                "mailbox": "stalwart",
                "googleWorkspace": "eligible",
            },
        },
        {
            "apiVersion": "governance.re8ch.com/v1alpha1",
            "kind": "GitWorkspace",
            "metadata": {"name": local_part, "namespace": "git-workspaces"},
            "spec": {
                "accountRef": local_part,
                "projectRef": name,
                "repository": spec["repository"]["url"],
                "folder": str(path_for(document)),
                "branchPrefix": f"workspace/{local_part}/",
            },
        },
    ]


def path_for(document: dict) -> Path:
    metadata, source = document["metadata"], document["spec"]["source"]
    if source["kind"] == "root":
        return Path(".")
    return Path("catalogs") / metadata["catalog"] / "modules" / metadata["name"] / source["path"]


def dump_documents(documents: list[dict]) -> str:
    return "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents)


def write_project(output: Path, path: Path, document: dict) -> None:
    target = output / ".re8ch"
    target.mkdir(parents=True, exist_ok=True)
    (target / "project.yaml").write_text(yaml.safe_dump(project_binding(path, document), sort_keys=False))
    (target / "cluster-resources.yaml").write_text(yaml.safe_dump(cluster_resource_index(document), sort_keys=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--module")
    parser.add_argument("--project-output", type=Path)
    parser.add_argument("--write-control-plane", action="store_true")
    args = parser.parse_args()
    modules = load_modules(args.root)

    if args.module:
        matches = [(path, doc) for path, doc in modules if doc["metadata"]["name"] == args.module]
        if len(matches) != 1 or args.project_output is None:
            raise SystemExit("--module requires one known module and --project-output")
        path, document = matches[0]
        write_project(args.project_output, path, document)

    if args.write_control_plane:
        resources = [resource for _, document in modules for resource in k8s_resources(document)]
        target = args.root / "platform/git-workspaces/project-accounts.yaml"
        target.write_text(dump_documents(resources))
        for path, document in modules:
            if document["spec"]["source"]["metadataPolicy"] != "control-plane-overlay":
                continue
            write_project(path.parent / "overlay", path, document)

    summary = {
        "modules": len(modules),
        "ownedAccounts": sum(doc["spec"]["ownership"] == "owned" for _, doc in modules),
        "projectWritten": args.module if args.module else None,
        "controlPlaneWritten": args.write_control_plane,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
