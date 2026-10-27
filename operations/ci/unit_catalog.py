"""Shared unit inputs and human-readable impact decisions; reuse remains disabled."""
from __future__ import annotations

import argparse
import sys
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Support both direct CLI execution and importlib-loaded validation consumers.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from operations.ci.input_snapshot import yaml_document

LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or (pattern.endswith("/**") and path == pattern[:-3])


def patterns(value):
    if not isinstance(value, list) or any(
        not isinstance(p, str) or not p or PurePosixPath(p).is_absolute()
        or ".." in PurePosixPath(p).parts or "\\" in p for p in value
    ):
        raise ValueError("unit inputs must be lists of repository-relative patterns")
    return value


def load_catalog(root=ROOT):
    catalog = yaml_document(root / "operations/ci/domain-catalog.yaml", loader=LOADER)
    defaults = catalog["unit_defaults"]
    for field in ("helpers", "environment"):
        patterns(defaults[field])
    for test, unit in catalog["validation_units"].items():
        patterns([test])
        if not test.startswith("tests/test_") or not test.endswith(".py") or not (root / test).is_file():
            raise ValueError(f"invalid validation unit: {test}")
        if set(unit) - {"roots", "reuse_blocked"} != {"sources", "data", "helpers", "tools", "reuse"}:
            raise ValueError(f"invalid unit fields: {test}")
        if "reuse_blocked" in unit and not isinstance(unit["reuse_blocked"], str):
            raise ValueError("reuse_blocked must explain why execution is required")
        for pattern in patterns(unit.get("roots", [])):
            if any(char in pattern for char in "*?["):
                raise ValueError(f"render roots cannot be patterns: {test}")
        for field in ("sources", "data", "helpers"):
            patterns(unit[field])
        if unit["reuse"] not in {"candidate", "never"}:
            raise ValueError(f"invalid reuse policy: {test}")
        if not isinstance(unit["tools"], list) or set(unit["tools"]) - {"python", "git", "helm", "kubectl", "kustomize", "node"}:
            raise ValueError(f"invalid tools: {test}")
    return catalog


def inputs_for(test, catalog):
    unit = catalog["validation_units"].get(test)
    if unit is None:
        return []
    defaults = catalog["unit_defaults"]
    return sorted(set([test, *defaults["helpers"], *defaults["environment"],
                       *unit["sources"], *unit["data"], *unit["helpers"],
                       *(root + "/**" for root in unit.get("roots", []))]))


def describe(plan, root=ROOT, catalog=None):
    """Describe selection separately from execution or future success reuse."""
    catalog = load_catalog(root) if catalog is None else catalog
    paths = plan.get("paths", [])
    selected = set(plan["tests"]) if plan["jobs"]["unit"] else set()
    tests = {str(path.relative_to(root)) for path in (root / "tests").glob("test_*.py")}
    records = {}
    for test in sorted(tests | selected):
        reasons = []
        if test in selected:
            if plan["full"]:
                common = catalog["shared_paths"] + catalog["unit_defaults"]["helpers"] + catalog["unit_defaults"]["environment"]
                shared = [p for p in paths if any(matches(p, pattern) for pattern in common)]
                reasons.append({"code": "full", "paths": shared, "unknown": plan["risky_unknown"],
                                "fallback": plan.get("fallback_reason"), "reason": plan.get("full_reason", "requested-full")})
            else:
                if test in catalog["always_tests"]:
                    reasons.append({"code": "baseline"})
                if test in paths:
                    reasons.append({"code": "changed-test", "paths": [test]})
                if test in plan.get("unit_reasons", {}):
                    reasons.append({"code": "unit-input", "paths": plan["unit_reasons"][test]})
                if test in plan.get("unit_roots", {}):
                    reasons.append({"code": "render-dependency", "roots": plan["unit_roots"][test]})
                for field, catalog_field in (("domains", "domains"), ("focused_suites", "focused_suites")):
                    for name in plan[field]:
                        config = catalog[catalog_field][name]
                        if test in config.get("tests", []):
                            reasons.append({"code": field, "name": name, "changed_paths": paths,
                                            "patterns": config["paths"]})
                            if field == "domains":
                                roots = {r: plan["dependency_reasons"][r] for r in config["roots"]
                                         if r in plan.get("dependency_reasons", {})}
                                if roots:
                                    reasons.append({"code": "base-head-dependencies", "roots": roots})
                if not reasons:
                    reasons.append({"code": "explicit-selection"})
        else:
            reasons.append({"code": "unit-disabled" if not plan["jobs"]["unit"] else "not-selected",
                            "changed_paths": paths, "input_patterns": inputs_for(test, catalog),
                            "domain_owners": [name for name, config in catalog["domains"].items()
                                              if test in config.get("tests", [])]})
        declared = test in catalog["validation_units"]
        records[test] = {
            "status": "planned" if test in selected else "unaffected",
            "reasons": reasons,
            "declaration": "reviewed" if declared else "legacy-domain-selection",
            "inputs": inputs_for(test, catalog),
            "reuse": catalog["validation_units"][test]["reuse"] if declared else "never",
        }
    return {"version": 1, "reuse_enabled": False, "plan": plan, "units": records}


def summary(report):
    # JSON is authoritative; escape all repository-controlled text in Markdown.
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("`", "&#96;").replace("\n", " ").replace("\r", " ")
    lines = ["### Unit selection (input-aware; reuse disabled)", "",
             "| Test file | Status | Selection reason | Declaration | Outcomes | Seconds |",
             "|---|---|---|---|---|---|"]
    for test, record in report["units"].items():
        reasons = "; ".join(json.dumps({key: value[:3] + [f"… {len(value)} total"]
                                        if isinstance(value, list) and len(value) > 3 else value
                                        for key, value in reason.items()}, ensure_ascii=False, sort_keys=True)
                            for reason in record["reasons"])
        seconds = sum(record.get("seconds", {}).values())
        lines.append("| " + " | ".join(map(cell, (test, record["status"], reasons, record["declaration"],
                                                  record.get("outcomes", "pending"), f"{seconds:.3f}"))) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-file", type=Path)
    args = parser.parse_args()
    report = describe(json.loads(args.plan_file.read_text() if args.plan_file else os.environ["PLAN"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as stream:
            stream.write(summary(report))


if __name__ == "__main__":
    main()
