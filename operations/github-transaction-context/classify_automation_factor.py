#!/usr/bin/env python3
"""Classify automation-authored PR ledger rows into bounded factor streams."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


POLICY = Path(__file__).with_name("automation-factor-policy.json")


def dependabot_ecosystem(head: str) -> str | None:
    match = re.fullmatch(r"dependabot/([^/]+)/.+", head)
    return match.group(1) if match else None


def classification(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    repository = record.get("repository") or record.get("repositoryWithOwner")
    author = record.get("author") or record.get("authorLogin")
    head = record.get("headRefName") or ""
    title = record.get("title") or ""
    result = {
        "repository": repository,
        "number": record.get("number"),
        "author": author,
        "decision": "review",
        "actorKind": "unknown-automation",
        "primaryFactorKey": None,
        "reason": policy["unknownAutomation"]["reason"],
    }
    if not repository or not author or not record.get("number"):
        result["reason"] = "ledger row lacks repository, number or author"
        return result

    dependabot = policy["actors"]["dependabot"]
    ecosystem = dependabot_ecosystem(head)
    if author in dependabot["authors"] and ecosystem:
        result.update({
            "decision": "aggregate",
            "actorKind": "dependabot",
            "primaryFactorKey": f"dependency-update:{repository}:{ecosystem}",
            "reason": "exact reviewed identity and standard Dependabot branch",
        })
        return result

    promoter = policy["actors"]["domain-promoter"]
    valid_promoter_branch = any(head.startswith(prefix) for prefix in promoter["headPrefixes"])
    valid_promoter_title = re.fullmatch(promoter["titlePattern"], title) is not None
    if author in promoter["authors"] and valid_promoter_branch and valid_promoter_title:
        result.update({
            "decision": "aggregate",
            "actorKind": "domain-promoter",
            "primaryFactorKey": f"domain-oci-promotion:{repository}",
            "reason": "exact reviewed identity, branch and promotion title",
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="JSON array audit ledger; defaults to stdin")
    parser.add_argument("--policy", type=Path, default=POLICY)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text())
    source = args.input.read_text() if args.input else sys.stdin.read()
    records = json.loads(source)
    if not isinstance(records, list):
        records = [records]
    print(json.dumps([classification(record, policy) for record in records], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
