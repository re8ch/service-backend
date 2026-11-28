#!/usr/bin/env python3
"""Validate and project verified architecture ledger records."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


class LedgerError(ValueError):
    pass


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(record, expected_repository=None, expected_sha=None, expected_cluster=None):
    schema = json.loads((Path(__file__).parent / "schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record), key=lambda error: list(error.path))
    if errors:
        raise LedgerError("record schema is invalid: " + errors[0].message)
    try:
        revision, deployment = record["revision"], record["deployment"]
        assert record["schema_version"] == "1.0"
        assert len(revision["source_sha"]) == 40 and int(revision["source_sha"], 16) >= 0
        assert deployment["status"] == "success"
        assert deployment["github_deployment_id"] > 0
        assert deployment["ssa_receipt"]["digest"].startswith("sha256:")
        assert len(deployment["ssa_receipt"]["digest"]) == 71
        live = deployment["live_observation"]
        assert live["result"] == "passed" and live["probes"]
        assert all(probe["result"] == "passed" for probe in live["probes"])
        observed = datetime.fromisoformat(deployment["observed_at"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(revision["created_at"].replace("Z", "+00:00"))
        assert observed.tzinfo and created.tzinfo and observed <= created
        assert record["architecture"]["components"]
        assert record["benchmark"]["metrics"]
        assert record["privacy"] == {"classification": "optimization-dataset", "redacted": True}
        if expected_repository:
            assert revision["source_repository"] == expected_repository
        if expected_sha:
            assert revision["source_sha"] == expected_sha
        if expected_cluster:
            assert deployment["cluster"] == expected_cluster
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        raise LedgerError("record lacks exact successful deployment evidence") from error
    encoded = json.dumps(record, sort_keys=True)
    forbidden = ("kubeconfig", "private_key", "private-key", "bearer ", "client_secret")
    if any(term in encoded.lower() for term in forbidden):
        raise LedgerError("record contains forbidden secret or topology material")
    return record


def compare(current, previous):
    if current["benchmark"]["profile_digest"] != previous["benchmark"]["profile_digest"]:
        return {"status": "not_comparable", "reason": "benchmark_profile_changed"}
    before, after = previous["benchmark"]["metrics"], current["benchmark"]["metrics"]
    shared = sorted(set(before) & set(after))
    return {"status": "comparable", "profile_digest": current["benchmark"]["profile_digest"], "metrics": {name: {"before": before[name], "after": after[name], "delta": after[name] - before[name], "delta_percent": None if before[name] == 0 else round((after[name] - before[name]) / before[name] * 100, 6)} for name in shared}}


def project(record, difference):
    return {
        "revision_id": record["revision"]["id"],
        "source_repository": record["revision"]["source_repository"],
        "source_sha": record["revision"]["source_sha"],
        "cluster": record["deployment"]["cluster"],
        "plane": record["deployment"]["plane"],
        "environment": record["deployment"]["environment"],
        "architecture_digest": record["architecture"]["digest"],
        "component_count": len(record["architecture"]["components"]),
        "benchmark_profile": record["benchmark"]["profile"],
        "benchmark_profile_digest": record["benchmark"]["profile_digest"],
        "metrics": record["benchmark"]["metrics"],
        "performance_difference": difference,
        "deployment_verified": True,
        "observed_at": record["deployment"]["observed_at"],
    }


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate")
    check.add_argument("record")
    check.add_argument("--repository")
    check.add_argument("--sha")
    check.add_argument("--cluster")
    publish = sub.add_parser("project")
    publish.add_argument("record")
    publish.add_argument("--previous")
    publish.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = validate(load(args.record), getattr(args, "repository", None), getattr(args, "sha", None), getattr(args, "cluster", None))
    if args.command == "validate":
        print(_digest(record))
        return
    difference = {"status": "first_revision"}
    if args.previous:
        difference = compare(record, validate(load(args.previous)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(project(record, difference), sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
