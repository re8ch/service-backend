#!/usr/bin/env python3
"""Validate that a public evidence envelope cannot carry a private witness."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "contracts/public-evidence-privacy/policy.yaml"
SCHEMA = ROOT / "schemas/public-evidence.schema.json"


class EvidenceError(ValueError):
    pass


VALUE_PATTERNS = {
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "ipv4": re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])"),
    "uuid": re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
    "precise-timestamp": re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"),
}


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def validate_document(document: object) -> None:
    policy = yaml.safe_load(POLICY.read_text())["spec"]
    schema = json.loads(SCHEMA.read_text())
    try:
        jsonschema.validate(document, schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise EvidenceError(f"schema-invalid:{location}") from None

    forbidden_keys = {key.casefold() for key in policy["forbiddenKeys"]}
    patterns = {name: VALUE_PATTERNS[name] for name in policy["forbiddenValueClasses"]}
    for key, value in _walk(document):
        if str(key).casefold() in forbidden_keys:
            raise EvidenceError("forbidden-key")
        if isinstance(value, str):
            for name, pattern in patterns.items():
                if pattern.search(value):
                    raise EvidenceError(f"forbidden-value:{name}")

    bucket = date.fromisoformat(document["timeBucket"])
    publish_after = date.fromisoformat(document["publishAfter"])
    minimum_delay = timedelta(hours=policy["aggregation"]["minimumPublicationDelayHours"])
    if publish_after - bucket < minimum_delay:
        raise EvidenceError("publication-delay-too-short")
    if document["cohortBand"] == "suppressed" and document["metricBand"] != "suppressed":
        raise EvidenceError("suppressed-cohort-leaks-metric")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        try:
            validate_document(yaml.safe_load(path.read_text()))
        except (EvidenceError, OSError, yaml.YAMLError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
