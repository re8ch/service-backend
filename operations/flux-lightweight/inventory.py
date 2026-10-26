#!/usr/bin/env python3
"""Inventory repository-declared Flux resources without cluster access."""

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name("controller-capabilities.yaml")
DEFAULT_SCAN_ROOTS = ("clusters/re8ch", "platform")
SKIP_PARTS = {".git", "templates"}
SKIP_NAMES = {"gotk-components.yaml"}


def api_group(api_version: str) -> str:
    return api_version.split("/", 1)[0] if "/" in api_version else "core"


def load_capabilities(path: Path = CATALOG) -> tuple[dict[tuple[str, str], str], dict[str, dict]]:
    catalog = yaml.load(path.read_text(), Loader=YAML_SAFE_LOADER)
    controllers = catalog["spec"]["controllers"]
    kinds: dict[tuple[str, str], str] = {}
    for controller, definition in controllers.items():
        for identity in definition["apiKinds"]:
            group, kind = identity.split("/", 1)
            key = (group, kind)
            if key in kinds:
                raise ValueError(f"duplicate Flux API mapping for {identity}")
            kinds[key] = controller
    return kinds, controllers


def yaml_paths(scan_roots: list[Path]):
    for scan_root in scan_roots:
        for path in sorted(scan_root.rglob("*.yaml")):
            relative = path.relative_to(ROOT)
            if path.name in SKIP_NAMES or any(part in SKIP_PARTS for part in relative.parts):
                continue
            yield path


def refs(spec: dict) -> dict:
    result = {}
    for field in ("sourceRef", "chartRef"):
        if isinstance(spec.get(field), dict):
            result[field] = spec[field]
    chart = spec.get("chart")
    if isinstance(chart, dict) and isinstance(chart.get("spec"), dict):
        source_ref = chart["spec"].get("sourceRef")
        if isinstance(source_ref, dict):
            result["chartSourceRef"] = source_ref
    depends_on = spec.get("dependsOn")
    if isinstance(depends_on, list):
        result["dependsOn"] = depends_on
    return result


def secret_refs(value, prefix="") -> list[dict]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else key
            if key.lower().endswith("secretref") and isinstance(child, dict) and child.get("name"):
                found.append({"field": child_path, "name": child["name"]})
            found.extend(secret_refs(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(secret_refs(child, f"{prefix}[{index}]"))
    return found


def inventory(scan_roots: list[Path]) -> dict:
    kind_map, controllers = load_capabilities()
    resources = []
    parse_errors = []
    for path in yaml_paths(scan_roots):
        try:
            documents = yaml.load_all(path.read_text(), Loader=YAML_SAFE_LOADER)
            for document_index, item in enumerate(documents, 1):
                if not isinstance(item, dict):
                    continue
                key = (api_group(str(item.get("apiVersion", ""))), str(item.get("kind", "")))
                controller = kind_map.get(key)
                if not controller:
                    continue
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
                resources.append({
                    "controller": controller,
                    "apiVersion": item.get("apiVersion"),
                    "kind": item.get("kind"),
                    "namespace": metadata.get("namespace", "default"),
                    "name": metadata.get("name"),
                    "file": str(path.relative_to(ROOT)),
                    "document": document_index,
                    "interval": spec.get("interval"),
                    "path": spec.get("path"),
                    "prune": spec.get("prune"),
                    "suspend": spec.get("suspend", False),
                    "references": refs(spec),
                    "secretReferences": secret_refs(spec),
                })
        except yaml.YAMLError as error:
            parse_errors.append({"file": str(path.relative_to(ROOT)), "error": str(error)})

    resources.sort(key=lambda item: (item["controller"], item["kind"], item["namespace"], item["name"] or "", item["file"]))
    by_controller = Counter(item["controller"] for item in resources)
    by_kind = Counter(item["kind"] for item in resources)
    identities: dict[tuple[str, str, str, str], list[str]] = {}
    for item in resources:
        identity = (item["apiVersion"], item["kind"], item["namespace"], item["name"] or "")
        identities.setdefault(identity, []).append(item["file"])
    duplicates = [
        {
            "apiVersion": identity[0],
            "kind": identity[1],
            "namespace": identity[2],
            "name": identity[3],
            "files": sorted(files),
        }
        for identity, files in identities.items()
        if len(files) > 1
    ]
    duplicates.sort(key=lambda item: (item["kind"], item["namespace"], item["name"]))
    unused = sorted(set(controllers) - set(by_controller))
    return {
        "scanRoots": [str(path.relative_to(ROOT)) for path in scan_roots],
        "resourceCount": len(resources),
        "uniqueResourceCount": len(identities),
        "countsByController": dict(sorted(by_controller.items())),
        "countsByKind": dict(sorted(by_kind.items())),
        "controllersWithoutDeclaredResources": unused,
        "duplicateIdentities": duplicates,
        "parseErrors": parse_errors,
        "resources": resources,
    }


def summary(report: dict) -> str:
    lines = [
        f"Flux declarations: {report['resourceCount']}",
        f"Unique Flux identities: {report['uniqueResourceCount']}",
    ]
    for controller, count in report["countsByController"].items():
        lines.append(f"  {controller}: {count}")
    lines.append("Kinds:")
    for kind, count in report["countsByKind"].items():
        lines.append(f"  {kind}: {count}")
    if report["controllersWithoutDeclaredResources"]:
        lines.append("No declared resources: " + ", ".join(report["controllersWithoutDeclaredResources"]))
    if report["duplicateIdentities"]:
        lines.append(f"Duplicate identities: {len(report['duplicateIdentities'])}")
    if report["parseErrors"]:
        lines.append(f"Parse errors: {len(report['parseErrors'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "summary"), default="summary")
    parser.add_argument("--scan-root", action="append", dest="scan_roots")
    args = parser.parse_args()
    relative_roots = args.scan_roots or list(DEFAULT_SCAN_ROOTS)
    scan_roots = [(ROOT / value).resolve() for value in relative_roots]
    for path in scan_roots:
        if not path.is_dir() or ROOT not in path.parents:
            parser.error(f"scan root must be a repository directory: {path}")
    report = inventory(scan_roots)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary(report))
    return 1 if report["parseErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

