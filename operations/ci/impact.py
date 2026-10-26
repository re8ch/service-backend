#!/usr/bin/env python3
"""Select the smallest safe CI surface for a control-plane change."""

from __future__ import annotations

import argparse
import sys
import fnmatch
import importlib.util
import json
import os
import posixpath
import subprocess
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

ROOT = Path(__file__).resolve().parents[2]
# Support both direct CLI execution and importlib-loaded validation consumers.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from operations.ci.input_snapshot import yaml_document

CATALOG = ROOT / "operations/ci/domain-catalog.yaml"
_SPEC = importlib.util.spec_from_file_location("ci_unit_catalog", Path(__file__).with_name("unit_catalog.py"))
unit_catalog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(unit_catalog)
_RENDER_SPEC = importlib.util.spec_from_file_location("ci_render_inputs", Path(__file__).with_name("render_evidence.py"))
render_inputs = importlib.util.module_from_spec(_RENDER_SPEC)
_RENDER_SPEC.loader.exec_module(render_inputs)


def changed_paths(base: str, head: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
        cwd=ROOT,
    )
    return sorted({os.fsdecode(path) for path in output.split(b"\0") if path})


def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path == pattern[:-3]
    )


def definitions_at(revision: str) -> dict:
    """Read historical Kustomize definitions in two Git calls; never execute base code."""
    entries = subprocess.check_output(["git", "ls-tree", "-rz", revision], cwd=ROOT).split(b"\0")
    definitions = []
    for entry in filter(None, entries):
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        path = name.decode()
        if mode == b"120000" and path.startswith(("clusters/", "platform/", "operations/")):
            raise ValueError("historical symlink inputs require full validation")
        if path.rsplit("/", 1)[-1] in {"kustomization.yaml", "kustomization.yml", "Kustomization"}:
            if kind != b"blob":
                raise ValueError("invalid historical definition")
            definitions.append((path, oid))
    data = subprocess.check_output(["git", "cat-file", "--batch"], cwd=ROOT,
                                   input=b"".join(oid + b"\n" for _, oid in definitions))
    result = {}
    for path, oid in definitions:
        header, data = data.split(b"\n", 1)
        actual, kind, size = header.split()
        if actual != oid or kind != b"blob":
            raise ValueError("invalid historical blob")
        size = int(size)
        root = path.rsplit("/", 1)[0]
        if root in result:
            raise ValueError("ambiguous historical definition")
        result[root] = yaml.load(data[:size], Loader=YAML_SAFE_LOADER) or {}
        data = data[size + 1:]
    return result


def root_affected(root: str, paths: list[str], visited: set[str] | None = None, *, definitions=None) -> bool:
    """Follow local Kustomize dependencies, including nested/shared bases."""
    visited = set() if visited is None else visited
    if root in visited:
        return False
    visited.add(root)
    if any(path == root or path.startswith(root + "/") for path in paths):
        return True
    base = ROOT / root
    if definitions is not None:
        if root not in definitions:
            return False
        config = definitions[root]
    else:
        files = [base / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")
                 if (base / name).is_file()]
        if not files:
            return False
        if len(files) != 1:
            raise ValueError(f"ambiguous definition: {root}")
        if files[0].resolve() != files[0]:
            raise ValueError("symlink definitions require full validation")
        config = yaml_document(files[0], loader=YAML_SAFE_LOADER) or {}
    if not isinstance(config, dict) or set(config) - render_inputs.FIELDS - {"helmCharts"}:
        raise ValueError(f"unsupported dependency fields: {root}")
    refs = [ref for field in ("resources", "components", "bases", "configurations", "crds")
            for ref in config.get(field, [])]
    for patch in config.get("patches", []) + config.get("patchesJson6902", []) + config.get("replacements", []):
        if isinstance(patch, dict) and patch.get("path"):
            refs.append(patch["path"])
    refs.extend(config.get("patchesStrategicMerge", []))
    if config.get("openapi", {}).get("path"):
        refs.append(config["openapi"]["path"])
    for chart in config.get("helmCharts", []):
        refs.extend(chart.get("additionalValuesFiles", []))
        if chart.get("valuesFile"):
            refs.append(chart["valuesFile"])
    for generator in config.get("configMapGenerator", []) + config.get("secretGenerator", []):
        refs.extend(value.split("=", 1)[-1] for value in generator.get("files", []))
        refs.extend(generator.get("envs", []))
    for ref in refs:
        if not isinstance(ref, str) or "://" in ref or "\n" in ref:
            continue
        relative = posixpath.normpath(posixpath.join(root, ref))
        if relative == ".." or relative.startswith(("../", "/")):
            raise SystemExit(f"Kustomize dependency escapes repository: {root}: {ref}")
        if definitions is None and not (ROOT / relative).resolve().is_relative_to(ROOT):
            raise SystemExit(f"Kustomize dependency escapes repository: {root}: {ref}")
        if definitions is None and (ROOT / relative).resolve() != ROOT / relative:
            raise ValueError("symlink dependencies require full validation")
        if any(path == relative or path.startswith(relative + "/") for path in paths):
            return True
        if root_affected(relative, paths, visited, definitions=definitions):
            return True
    return False


def load_catalog() -> dict:
    catalog = yaml_document(CATALOG, loader=YAML_SAFE_LOADER)
    if catalog.get("version") != 1 or not catalog.get("domains"):
        raise SystemExit("invalid CI domain catalog")
    declared = [root for domain in catalog["domains"].values() for root in domain["roots"]]
    duplicates = sorted({root for root in declared if declared.count(root) > 1})
    actual = sorted(
        str(path.parent.relative_to(ROOT))
        for tree in (ROOT / "clusters", ROOT / "platform")
        for path in tree.rglob("kustomization.yaml")
        if not (
            path.parent.name == "source"
            and path.parent.parent.parent == ROOT / "clusters/re8ch/domains"
        )
    )
    # A small number of operational controllers are deployed directly from
    # operations/. They remain explicit domain roots rather than making every
    # helper kustomization deployable by implication.
    actual.extend(
        root for root in declared
        if root.startswith("operations/") and (ROOT / root / "kustomization.yaml").is_file()
    )
    actual = sorted(set(actual))
    missing = sorted(set(actual) - set(declared) - {"clusters/re8ch", "clusters/re8ch/flux-system/github-canary", "clusters/re8ch/domain-sources"})
    stale = sorted(set(declared) - set(actual))
    if set(catalog.get("external_inputs", {})) - set(declared):
        raise SystemExit("external input has no reviewed deployable root owner")
    if duplicates or missing or stale:
        raise SystemExit(f"invalid root ownership: duplicates={duplicates}, missing={missing}, stale={stale}")
    return catalog


def select(paths: list[str], full: bool = False, *, base_definitions=None, fallback_reason=None) -> dict:
    source_paths = paths
    catalog = load_catalog()
    unit_catalog.load_catalog(ROOT)  # Fail closed on stale or malformed declarations.
    domains = catalog["domains"]
    focused_suites = catalog.get("focused_suites", {})
    ci_metadata_paths = catalog.get("ci_metadata_paths", [])
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
    common = catalog["unit_defaults"]["helpers"] + catalog["unit_defaults"]["environment"]
    shared = bool(full or fallback_reason or any(matches(path, pattern) for path in paths
                                                for pattern in catalog["shared_paths"] + common))
    dependency_reasons = {}

    def affected(root, changed=paths):
        head_hit = root_affected(root, changed)
        base_hit = base_definitions is not None and root_affected(root, changed, definitions=base_definitions)
        if head_hit or base_hit:
            previous = dependency_reasons.setdefault(root, {"head": False, "base": False})
            previous["head"] |= bool(head_hit)
            previous["base"] |= bool(base_hit)
        return head_hit or base_hit
    selected = set(domains) if shared else {
        name
        for name, config in domains.items()
        if any(matches(path, pattern) for path in paths for pattern in config["paths"])
    }
    if not shared:
        selected.update(name for name, config in domains.items() if any(affected(root) for root in config["roots"]))

    owned = lambda path: any(
        matches(path, pattern)
        for config in domains.values()
        for pattern in config["paths"]
    ) or any(matches(path, pattern) for pattern in catalog["shared_paths"]) \
        or any(
            matches(path, pattern)
            for config in focused_suites.values()
            for pattern in config["paths"]
        ) \
        or any(matches(path, pattern) for pattern in ci_metadata_paths) \
        or any(path == pattern for test in catalog["validation_units"]
               for pattern in unit_catalog.inputs_for(test, catalog))
    risky_unknown = [
        path for path in paths
        if (path.startswith(("clusters/", "platform/", "operations/", "schemas/", ".github/"))
            or (path.startswith("tests/") and not path.startswith("tests/test_"))
            or (path.startswith("tests/test_") and path.endswith(".py") and not (ROOT / path).is_file())
            or path.endswith((".yaml", ".yml", ".json"))) and not owned(path)
    ]
    if risky_unknown:
        selected = set(domains)
        shared = True

    changed_tests = [
        path for path in paths
        if path.startswith("tests/test_") and path.endswith(".py") and (ROOT / path).is_file()
    ]
    tests = list(catalog["always_tests"])
    # Consumer input patterns select tests but do NOT establish ownership of
    # unknown source/manifests. Broad scan scopes must not disable full fallback.
    unit_reasons = {
        test: sorted(path for path in source_paths
                     if any(matches(path, pattern) for pattern in unit_catalog.inputs_for(test, catalog)))
        for test in catalog["validation_units"] if test not in catalog["always_tests"]
    }
    unit_reasons = {test: changed for test, changed in unit_reasons.items() if changed}
    tests.extend(unit_reasons)
    unit_roots = {} if shared else {
        test: {root: dependency_reasons[root] for root in unit.get("roots", []) if affected(root, source_paths)}
        for test, unit in catalog["validation_units"].items() if unit.get("roots")
    }
    unit_roots = {test: roots for test, roots in unit_roots.items() if roots}
    tests.extend(unit_roots)
    selected_suites = sorted(
        name
        for name, config in focused_suites.items()
        if any(matches(path, pattern) for path in paths for pattern in config["paths"])
    )
    for name in selected_suites:
        tests.extend(focused_suites[name]["tests"])
    roots: list[str] = []
    submodules: list[str] = []
    for name in sorted(selected):
        config = domains[name]
        tests.extend(config.get("tests", []))
        domain_roots = config.get("roots", [])
        touched_roots = [] if shared else [root for root in domain_roots if affected(root)]
        roots.extend(domain_roots if shared or not touched_roots else touched_roots)
        submodules.extend(config.get("submodules", []))
    if shared:
        tests = [str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_*.py")]
    else:
        tests.extend(changed_tests)

    # Keep original paths: release locks and external chart inputs are mapped
    # to synthetic roots above, but checks consume the actual files as well.
    checks = {
        name: bool(shared or any(matches(path, pattern)
                                 for path in source_paths for pattern in patterns))
        for name, patterns in catalog["check_paths"].items()
    }
    checks["helm_inventory"] |= bool(roots)
    checks["federation"] |= bool(roots)
    if not checks["domain_bundles"]:
        sources = {str(path.parent.relative_to(ROOT))
                   for path in (ROOT / "clusters/re8ch/domains").glob("*/source/kustomization.yaml")}
        sources.update(root for root in (base_definitions or {})
                       if root.startswith("clusters/re8ch/domains/") and root.endswith("/source"))
        checks["domain_bundles"] = any(affected(root, source_paths) for root in sorted(sources))
    jobs = {
        "contract": any(checks[key] for key in ("workspace", "helm_inventory", "commerce")),
        "schema": any(checks[key] for key in ("schemas", "fabric", "encrypted", "public_authority")),
        "unit": bool(selected or shared or changed_tests or selected_suites or unit_reasons or unit_roots),
        "render": bool(roots or checks["domain_bundles"] or checks["federation"]),
        "k6-contracts": bool(shared or checks["k6"] or "observability" in selected),
        "dependency-review": bool(shared),
        "supply-chain": bool(shared or selected.intersection({"delivery", "tenant-runtime"})),
        "tenant-select": bool(shared or "tenant-runtime" in selected),
    }
    result = {
        "paths": sorted(source_paths),
        "unit_reasons": unit_reasons,
        "unit_roots": unit_roots,
        "dependency_reasons": dependency_reasons,
        "fallback_reason": fallback_reason,
        "full_reason": (fallback_reason or ("requested-full" if full else
                        "unknown-input" if risky_unknown else "shared-input")) if shared else None,
        "jobs": jobs,
        "checks": checks,
        "domains": sorted(selected),
        "focused_suites": selected_suites,
        "tests": sorted(set(tests)),
        "roots": sorted(set(roots)),
        "submodules": sorted(set(submodules)),
        "full": shared,
        "risky_unknown": risky_unknown,
        "run": jobs["unit"],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    base, base_definitions, fallback = None, None, None
    if args.paths is not None:
        paths = args.paths
    else:
        if not args.base:
            parser.error("--base or --path is required")
        paths = []
        try:
            paths = changed_paths(args.base, args.head)
            base = subprocess.check_output(["git", "merge-base", args.base, args.head], cwd=ROOT, text=True).strip()
            base_definitions = definitions_at(base)
        except (subprocess.SubprocessError, OSError, ValueError, yaml.YAMLError):
            fallback = "base-dependencies-unavailable"
    try:
        result = select(paths, args.full, base_definitions=base_definitions, fallback_reason=fallback)
    except (ValueError, TypeError, AttributeError, yaml.YAMLError):
        result = select(paths, True, fallback_reason="dependencies-unreadable")
    result["dependency_base"] = base
    compact = json.dumps(result, separators=(",", ":"))
    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            for key in ("domains", "tests", "roots", "submodules", "jobs", "checks"):
                handle.write(f"{key}={json.dumps(result[key], separators=(',', ':'))}\n")
            handle.write(f"run={str(result['run']).lower()}\n")
            handle.write(f"full={str(result['full']).lower()}\n")
            handle.write(f"plan={compact}\n")


if __name__ == "__main__":
    main()
