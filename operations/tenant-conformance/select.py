#!/usr/bin/env python3
"""Select tenant conformance modules from changed paths and declared dependencies."""

import argparse
import fnmatch
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "operations/tenant-conformance/modules.yaml"


def load_manifest():
    return yaml.safe_load(MANIFEST.read_text())


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def changed_paths(base, head):
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"], cwd=ROOT,
        check=True, text=True, capture_output=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def select(manifest, paths):
    modules = manifest["modules"]
    tenant_paths = sorted({pattern for value in modules.values() for pattern in value.get("owns", [])})
    relevant = [path for path in paths if matches(path, tenant_paths + manifest.get("shared", []))]
    unowned = [path for path in relevant if not matches(path, tenant_paths) and not matches(path, manifest.get("shared", []))]
    if unowned:
        raise SystemExit("unowned tenant paths: " + ", ".join(unowned))
    direct = {name for name, value in modules.items() if any(matches(path, value.get("owns", [])) for path in paths)}
    if any(matches(path, manifest.get("shared", [])) for path in paths):
        direct.add("tenant-core")
    selected = set(direct)
    # A module change runs that module plus its prerequisites. A direct shared
    # contract/core change additionally runs the reverse dependency closure.
    changed = True
    while changed:
        changed = False
        for name, value in modules.items():
            if name in selected:
                for dependency in value.get("dependsOn", []):
                    if dependency not in selected:
                        selected.add(dependency); changed = True
            elif "tenant-core" in direct and any(dependency in selected for dependency in value.get("dependsOn", [])):
                selected.add(name); changed = True
    return sorted(selected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD^1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--paths", nargs="*")
    parser.add_argument("--non-tenant-tests", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest()
    tenant_tests = {test for value in manifest["modules"].values() for test in value.get("tests", [])}
    if args.non_tenant_tests:
        print(" ".join(str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests").glob("test_*.py")) if str(path.relative_to(ROOT)) not in tenant_tests))
        return
    paths = args.paths if args.paths is not None else changed_paths(args.base, args.head)
    selected = select(manifest, paths)
    matrix = [{"module": name, "tests": " ".join(manifest["modules"][name]["tests"])} for name in selected]
    payload = json.dumps(matrix, separators=(",", ":"))
    print(payload)
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write(f"matrix={payload}\n")


if __name__ == "__main__":
    main()
