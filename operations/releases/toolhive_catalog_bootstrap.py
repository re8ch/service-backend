#!/usr/bin/env python3
"""Materialize the exact one-time ToolHive catalog bootstrap inventory."""
import argparse
import json
import tarfile
from pathlib import Path

import yaml

NAMESPACE = "toolhive-system"
RELEASE_ID = "agent-runtime-domain-toolhive-system"
CATALOG = ("v1", "ConfigMap", "re8ch-approved-tool-catalog")
EXECUTOR_ROLE = ("rbac.authorization.k8s.io/v1", "Role", RELEASE_ID)


def identity(obj):
    metadata = obj.get("metadata", {})
    return obj.get("apiVersion"), obj.get("kind"), metadata.get("name")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--access", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with tarfile.open(args.bundle, "r:gz") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        objects = json.load(archive.extractfile("objects.json"))
    if manifest.get("releaseId") != RELEASE_ID:
        raise SystemExit("wrong releaseId")

    catalogs = [obj for obj in objects if identity(obj) == CATALOG]
    if len(catalogs) != 1 or catalogs[0].get("metadata", {}).get("namespace") != NAMESPACE:
        raise SystemExit("signed bundle must contain exactly one approved ToolHive catalog")
    if set(catalogs[0].get("data", {})) != {"registry.json"}:
        raise SystemExit("approved ToolHive catalog must contain only registry.json")

    roles = [obj for obj in yaml.safe_load_all(args.access.read_text()) if obj and identity(obj) == EXECUTOR_ROLE]
    if len(roles) != 1 or roles[0].get("metadata", {}).get("namespace") != NAMESPACE:
        raise SystemExit("domain access must contain exactly one ToolHive executor Role")
    catalog_grants = [
        rule for rule in roles[0].get("rules", [])
        if "configmaps" in rule.get("resources", []) and CATALOG[2] in rule.get("resourceNames", [])
    ]
    if len(catalog_grants) != 1 or not {"get", "patch", "update"}.issubset(catalog_grants[0].get("verbs", [])):
        raise SystemExit("ToolHive executor Role does not grant exact catalog takeover access")

    args.output.write_text(yaml.safe_dump_all([roles[0], catalogs[0]], sort_keys=False, explicit_start=True))


if __name__ == "__main__":
    main()
