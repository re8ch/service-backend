#!/usr/bin/env python3
"""Validate normalized human pull-request factor records."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


MODEL = Path(__file__).with_name("factor-model.json")
FACTOR_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$")


def issue_number(ref: str) -> str:
    return ref.rsplit("#", 1)[-1]


def complete_project(project: dict[str, Any], model: dict[str, Any]) -> bool:
    fields = project.get("fields", {})
    return (
        all(fields.get(name) for name in model["requiredProjectFields"])
        and any(fields.get(name) for name in model["completionDateFields"])
    )


def audit(record: dict[str, Any], model: dict[str, Any]) -> list[str]:
    if record.get("authorKind") == "automation":
        return []

    identity = f"{record.get('repository', '<unknown>')}#{record.get('number', '?')}"
    factors = record.get("factors", [])
    failures: list[str] = []
    refs = [factor.get("ref") for factor in factors]
    if len(refs) != len(set(refs)):
        failures.append(f"{identity}: factor references must be unique")
    for factor in factors:
        if not FACTOR_REF.fullmatch(factor.get("ref", "")):
            failures.append(f"{identity}: factor ref must use owner/repository#number")

    primary = [factor for factor in factors if factor.get("role") == "primary"]
    if len(primary) != model["primary"]["cardinality"]:
        failures.append(f"{identity}: exactly one primary factor is required")
    else:
        factor = primary[0]
        if factor.get("relationship") != model["primary"]["relationship"]:
            failures.append(f"{identity}: primary factor must close the PR")
        if not factor.get("issueType"):
            failures.append(f"{identity}: primary factor requires a native Issue Type")
        parent = factor.get("parent") or {}
        if parent.get("kind") not in {"root", "issue"}:
            failures.append(f"{identity}: primary factor requires parent Issue or explicit root")
        if parent.get("kind") == "issue" and not FACTOR_REF.fullmatch(parent.get("ref", "")):
            failures.append(f"{identity}: parent factor must use owner/repository#number")
        if not any(complete_project(project, model) for project in factor.get("projects", [])):
            failures.append(f"{identity}: primary factor requires one complete lifecycle Project")
        if issue_number(factor.get("ref", "#")) not in record.get("headRefName", ""):
            failures.append(f"{identity}: branch must retain the primary Issue number")

    allowed_secondary = set(model["secondary"]["relationships"])
    for factor in factors:
        if factor.get("role") == "primary":
            continue
        if factor.get("role") != "secondary":
            failures.append(f"{identity}: factor role must be primary or secondary")
        elif factor.get("relationship") not in allowed_secondary:
            failures.append(f"{identity}: unsupported secondary relationship")
    return failures


def summarize(records: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    audited = [record for record in records if record.get("authorKind") != "automation"]
    results = [
        {"repository": record.get("repository"), "number": record.get("number"), "failures": audit(record, model)}
        for record in audited
    ]
    return {
        "audited": len(results),
        "complete": sum(not result["failures"] for result in results),
        "incomplete": sum(bool(result["failures"]) for result in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--model", type=Path, default=MODEL)
    args = parser.parse_args()
    summary = summarize(json.loads(args.ledger.read_text()), json.loads(args.model.read_text()))
    print(json.dumps(summary, indent=2))
    return bool(summary["incomplete"])


if __name__ == "__main__":
    raise SystemExit(main())
