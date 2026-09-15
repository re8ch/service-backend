#!/usr/bin/env python3
"""Select the smallest safe CI surface for a control-plane change."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "operations/ci/domain-catalog.yaml"


def changed_paths(base: str, head: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        cwd=ROOT,
        text=True,
    )
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path == pattern[:-3]
    )


def root_affected(root: str, paths: list[str], visited: set[str] | None = None) -> bool:
    """Follow local Kustomize dependencies, including nested/shared bases."""
    visited = set() if visited is None else visited
    if root in visited:
        return False
    visited.add(root)
    if any(path == root or path.startswith(root + "/") for path in paths):
        return True
    base = ROOT / root
    definition = base / "kustomization.yaml"
    if not definition.is_file():
        return False
    config = yaml.safe_load(definition.read_text()) or {}
    refs = list(config.get("resources", [])) + list(config.get("components", [])) + list(config.get("bases", []))
    for patch in config.get("patches", []):
        if isinstance(patch, dict) and patch.get("path"):
            refs.append(patch["path"])
    refs.extend(config.get("patchesStrategicMerge", []))
    for chart in config.get("helmCharts", []):
        refs.extend(chart.get("additionalValuesFiles", []))
        if chart.get("valuesFile"):
            refs.append(chart["valuesFile"])
    for generator in config.get("configMapGenerator", []) + config.get("secretGenerator", []):
        refs.extend(value.split("=", 1)[-1] for value in generator.get("files", []))
        refs.extend(generator.get("envs", []))
    for ref in refs:
        if not isinstance(ref, str) or "://" in ref:
            continue
        target = (base / ref).resolve()
        if not target.is_relative_to(ROOT):
            raise SystemExit(f"Kustomize dependency escapes repository: {root}: {ref}")
        relative = str(target.relative_to(ROOT))
        if relative in paths or (target.is_dir() and root_affected(relative, paths, visited)):
            return True
    return False


def load_catalog() -> dict:
    catalog = yaml.safe_load(CATALOG.read_text())
    if catalog.get("version") != 1 or not catalog.get("domains"):
        raise SystemExit("invalid CI domain catalog")
    declared = [root for domain in catalog["domains"].values() for root in domain["roots"]]
    duplicates = sorted({root for root in declared if declared.count(root) > 1})
    actual = sorted(
        str(path.parent.relative_to(ROOT))
        for tree in (ROOT / "clusters", ROOT / "platform")
        for path in tree.rglob("kustomization.yaml")
    )
    missing = sorted(set(actual) - set(declared) - {"clusters/re8ch", "clusters/re8ch/flux-system/github-canary", "clusters/re8ch/domain-sources"})
    stale = sorted(set(declared) - set(actual))
    if set(catalog.get("external_inputs", {})) - set(declared):
        raise SystemExit("external input has no reviewed deployable root owner")
    if duplicates or missing or stale:
        raise SystemExit(f"invalid root ownership: duplicates={duplicates}, missing={missing}, stale={stale}")
    return catalog


def select(paths: list[str], full: bool = False) -> dict:
    catalog = load_catalog()
    domains = catalog["domains"]
    release_roots = {root.replace("/", "-") + ".yaml": root for config in domains.values() for root in config["roots"]}
    mapped = []
    has_release_lock = False
    for path in paths:
        chart_owners = [root for root, patterns in catalog.get("external_inputs", {}).items()
                        if any(matches(path, pattern) for pattern in patterns)]
        if chart_owners:
            mapped.extend(root + "/.external-input" for root in chart_owners)
            continue
        if path.startswith(("releases/", "clusters/re8ch/domain-sources/")) and path.split("/")[-1] in release_roots:
            mapped.append(release_roots[path.split("/")[-1]] + "/.release-lock")
            has_release_lock = True
        else:
            mapped.append(path)
    if has_release_lock:
        mapped = [path for path in mapped if path != "operations/releases/codenames.yaml"]
    paths = mapped
    shared = full or any(matches(path, pattern) for path in paths for pattern in catalog["shared_paths"])
    selected = set(domains) if shared else {
        name
        for name, config in domains.items()
        if any(matches(path, pattern) for path in paths for pattern in config["paths"])
    }
    if not shared:
        selected.update(name for name, config in domains.items() if any(root_affected(root, paths) for root in config["roots"]))

    owned = lambda path: any(
        matches(path, pattern)
        for config in domains.values()
        for pattern in config["paths"]
    ) or any(matches(path, pattern) for pattern in catalog["shared_paths"])
    risky_unknown = [
        path for path in paths
        if (path.startswith(("clusters/", "platform/", "operations/", "schemas/", ".github/"))
            or path.endswith((".yaml", ".yml", ".json"))) and not owned(path)
    ]
    if risky_unknown:
        selected = set(domains)
        shared = True

    tests = list(catalog["always_tests"])
    roots: list[str] = []
    submodules: list[str] = []
    for name in sorted(selected):
        config = domains[name]
        tests.extend(config.get("tests", []))
        domain_roots = config.get("roots", [])
        touched_roots = [root for root in domain_roots if root_affected(root, paths)]
        roots.extend(domain_roots if shared or not touched_roots else touched_roots)
        submodules.extend(config.get("submodules", []))
    if shared:
        tests = [str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_*.py")]
    else:
        tests.extend(path for path in paths if path.startswith("tests/test_") and path.endswith(".py") and (ROOT / path).is_file())

    result = {
        "domains": sorted(selected),
        "tests": sorted(set(tests)),
        "roots": sorted(set(roots)),
        "submodules": sorted(set(submodules)),
        "full": shared,
        "risky_unknown": risky_unknown,
        "run": bool(selected or paths),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    paths = args.paths if args.paths is not None else changed_paths(args.base, args.head)
    result = select(paths, args.full)
    compact = json.dumps(result, separators=(",", ":"))
    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            for key in ("domains", "tests", "roots", "submodules"):
                handle.write(f"{key}={json.dumps(result[key], separators=(',', ':'))}\n")
            handle.write(f"run={str(result['run']).lower()}\n")
            handle.write(f"full={str(result['full']).lower()}\n")
            handle.write(f"plan={compact}\n")


if __name__ == "__main__":
    main()
