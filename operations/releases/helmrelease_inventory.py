#!/usr/bin/env python3
"""Build a secret-safe inventory of repository-declared HelmReleases."""

import argparse
import hashlib
import json
from pathlib import Path

import yaml

# LibYAML keeps SafeLoader constructors; pure Python remains a supported fallback.
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOTS = ("clusters/re8ch", "platform")
DECISIONS = ROOT / "operations/releases/helmrelease-classifications.yaml"
DEFAULT_INVENTORY = ROOT / "operations/releases/helmrelease-inventory.yaml"
SKIP_NAMES = {"gotk-components.yaml"}
SKIP_PARTS = {".git", "templates"}
# Generated transition mirror: its HelmRelease documents are byte-identical to
# the five source domains and must not be counted as new lifecycle consumers.
SKIP_PREFIXES = {Path("clusters/re8ch/domains/main-legacy")}
ALLOWED_DISPOSITIONS = {
    "retain-helm-lifecycle",
    "bundle-ssa-candidate",
    "blocked-pending-evidence",
}
HELM_RELEASE_API_VERSIONS = {
    "helm.toolkit.fluxcd.io/v2",
    "helm.toolkit.fluxcd.io/v2beta2",
    "helm.toolkit.fluxcd.io/v2beta1",
}


def digest(value) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()


def yaml_paths(scan_roots: list[Path]):
    seen = set()
    for scan_root in scan_roots:
        for path in sorted(scan_root.rglob("*.yaml")):
            relative = path.relative_to(ROOT)
            if (path in seen or path.name in SKIP_NAMES or any(part in SKIP_PARTS for part in relative.parts)
                    or any(relative.is_relative_to(prefix) for prefix in SKIP_PREFIXES)):
                continue
            seen.add(path)
            yield path


def value_references(spec: dict) -> list[dict]:
    references = []
    for item in spec.get("valuesFrom", []) or []:
        if not isinstance(item, dict):
            continue
        references.append({
            "kind": item.get("kind", "ConfigMap"),
            "name": item.get("name"),
            "valuesKey": item.get("valuesKey", "values.yaml"),
            "optional": item.get("optional", False),
        })
    return references


def chart_contract(spec: dict) -> dict:
    chart_ref = spec.get("chartRef")
    if isinstance(chart_ref, dict):
        return {
            "mode": "chartRef",
            "kind": chart_ref.get("kind"),
            "name": chart_ref.get("name"),
            "namespace": chart_ref.get("namespace"),
        }
    chart = spec.get("chart") if isinstance(spec.get("chart"), dict) else {}
    chart_spec = chart.get("spec") if isinstance(chart.get("spec"), dict) else {}
    source_ref = chart_spec.get("sourceRef") if isinstance(chart_spec.get("sourceRef"), dict) else {}
    return {
        "mode": "template",
        "chart": chart_spec.get("chart"),
        "version": chart_spec.get("version"),
        "sourceRef": {
            "kind": source_ref.get("kind"),
            "name": source_ref.get("name"),
            "namespace": source_ref.get("namespace"),
        },
    }


def lifecycle(spec: dict) -> dict:
    install = spec.get("install") if isinstance(spec.get("install"), dict) else {}
    upgrade = spec.get("upgrade") if isinstance(spec.get("upgrade"), dict) else {}
    rollback = spec.get("rollback") if isinstance(spec.get("rollback"), dict) else {}
    test = spec.get("test") if isinstance(spec.get("test"), dict) else {}
    return {
        "install": {
            "crds": install.get("crds"),
            "remediation": install.get("remediation"),
        },
        "upgrade": {
            "crds": upgrade.get("crds"),
            "cleanupOnFail": upgrade.get("cleanupOnFail", False),
            "remediation": upgrade.get("remediation"),
        },
        "rollback": rollback,
        "testsEnabled": test.get("enable", False),
        "postRenderers": bool(spec.get("postRenderers")),
        "dependsOn": spec.get("dependsOn", []),
    }


def stable_id(namespace: str, name: str) -> str:
    return f"helmrelease:{namespace}:{name}"


def declarations(scan_roots: list[Path]) -> tuple[dict[str, dict], list[dict]]:
    releases: dict[str, dict] = {}
    parse_errors = []
    for path in yaml_paths(scan_roots):
        try:
            documents = yaml.load_all(path.read_text(encoding="utf-8"), Loader=YAML_SAFE_LOADER)
            for document_number, document in enumerate(documents, 1):
                if not isinstance(document, dict) or document.get("kind") != "HelmRelease":
                    continue
                if document.get("apiVersion") not in HELM_RELEASE_API_VERSIONS:
                    continue
                metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
                spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
                namespace = metadata.get("namespace", "default")
                name = metadata.get("name")
                identity = stable_id(namespace, name)
                occurrence = {"file": str(path.relative_to(ROOT)), "document": document_number}
                contract = {
                    "id": identity,
                    "apiVersion": document.get("apiVersion"),
                    "namespace": namespace,
                    "name": name,
                    "releaseName": spec.get("releaseName", name),
                    "targetNamespace": spec.get("targetNamespace", namespace),
                    "storageNamespace": spec.get("storageNamespace", namespace),
                    "suspended": spec.get("suspend", False),
                    "chart": chart_contract(spec),
                    "values": {
                        "inlinePresent": "values" in spec,
                        "inlineSha256": digest(spec.get("values")) if "values" in spec else None,
                        "references": value_references(spec),
                    },
                    "lifecycle": lifecycle(spec),
                    "maxHistory": spec.get("maxHistory"),
                    "serviceAccountName": spec.get("serviceAccountName"),
                    "declarationSha256": digest(document),
                    "occurrences": [occurrence],
                }
                if identity in releases:
                    existing = releases[identity]
                    if existing["declarationSha256"] != contract["declarationSha256"]:
                        raise ValueError(f"conflicting declarations for {identity}")
                    existing["occurrences"].append(occurrence)
                else:
                    releases[identity] = contract
        except (yaml.YAMLError, ValueError) as error:
            parse_errors.append({"file": str(path.relative_to(ROOT)), "error": str(error)})
    return releases, parse_errors


def load_decisions(path: Path) -> dict[str, dict]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=YAML_SAFE_LOADER)
    entries = document.get("releases", []) if isinstance(document, dict) else []
    decisions = {}
    for entry in entries:
        identity = entry.get("id") if isinstance(entry, dict) else None
        if not identity or identity in decisions:
            raise ValueError(f"classification IDs must be present and unique: {identity}")
        disposition = entry.get("disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            raise ValueError(f"invalid disposition for {identity}: {disposition}")
        if not entry.get("rollbackOwner") or not entry.get("evidence"):
            raise ValueError(f"classification requires rollbackOwner and evidence: {identity}")
        decisions[identity] = entry
    return decisions


def build(scan_roots: list[Path], decisions_path: Path = DECISIONS) -> dict:
    releases, parse_errors = declarations(scan_roots)
    decisions = load_decisions(decisions_path)
    missing = sorted(set(releases) - set(decisions))
    stale = sorted(set(decisions) - set(releases))
    records = []
    for identity in sorted(releases):
        record = releases[identity]
        decision = decisions.get(identity)
        if decision:
            record = {**record, "classification": decision}
        records.append(record)
    return {
        "apiVersion": "re8ch.com/v1alpha1",
        "kind": "HelmReleaseInventory",
        "source": "git",
        "scanRoots": [str(path.relative_to(ROOT)) for path in scan_roots],
        "releaseCount": len(records),
        "classificationComplete": not missing and not stale and not parse_errors,
        "missingClassifications": missing,
        "staleClassifications": stale,
        "parseErrors": parse_errors,
        "releases": records,
    }


def render(report: dict) -> str:
    return yaml.safe_dump(report, sort_keys=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "yaml", "summary"), default="summary")
    parser.add_argument("--check", action="store_true", help="fail unless classifications and checked inventory are current")
    parser.add_argument("--write", action="store_true", help="write the deterministic inventory artifact")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    parser.add_argument("--scan-root", action="append", dest="scan_roots")
    args = parser.parse_args()
    roots = [(ROOT / item).resolve() for item in (args.scan_roots or DEFAULT_ROOTS)]
    for path in roots:
        if not path.is_dir() or ROOT not in path.parents:
            parser.error(f"scan root must be a repository directory: {path}")
    report = build(roots, args.decisions)
    rendered = render(report)
    inventory_current = args.inventory.exists() and args.inventory.read_text(encoding="utf-8") == rendered
    if args.write:
        args.inventory.write_text(rendered, encoding="utf-8")
        inventory_current = True
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "yaml":
        print(rendered, end="")
    else:
        print(f"HelmReleases: {report['releaseCount']}")
        print(f"Classification complete: {str(report['classificationComplete']).lower()}")
        print(f"Missing classifications: {len(report['missingClassifications'])}")
        print(f"Stale classifications: {len(report['staleClassifications'])}")
        print(f"Parse errors: {len(report['parseErrors'])}")
        print(f"Checked inventory current: {str(inventory_current).lower()}")
    return 1 if args.check and (not report["classificationComplete"] or not inventory_current) else 0


if __name__ == "__main__":
    raise SystemExit(main())
