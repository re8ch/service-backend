#!/usr/bin/env python3
"""Validate the static-first security declaration without contacting GitHub."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_MUTATION = {
    "secret_scanning": "enabled",
    "secret_scanning_push_protection": "enabled",
}
EXPECTED_EXCLUSIONS = {
    "secret_scanning_ai_detection",
    "dependabot_security_updates",
    "code_security",
    "code_scanning_default_setup",
}


def validate(record: dict) -> None:
    delivery = record["delivery"]
    for field in (
        "workflowsAdded",
        "requiredChecksAdded",
        "codeqlSetupsAdded",
        "repositoryBuildMinutesAdded",
    ):
        if delivery[field] != 0:
            raise ValueError(f"static-first delivery requires {field}=0")

    rollout = record["publicRollout"]
    if rollout["mutation"] != EXPECTED_MUTATION:
        raise ValueError("public rollout may only enable secret scanning and push protection")
    if set(rollout["excludedMutations"]) != EXPECTED_EXCLUSIONS:
        raise ValueError("paid, AI, CodeQL and PR-producing mutations must stay excluded")
    if rollout["rollbackOrder"] != [
        "secret_scanning_push_protection=disabled",
        "secret_scanning=disabled",
    ]:
        raise ValueError("rollback must disable push protection before secret scanning")

    repositories = [
        repository
        for batch in rollout["batches"]
        for repository in batch["repositories"]
    ]
    if len(repositories) != 8 or len(set(repositories)) != 8:
        raise ValueError("public rollout must contain eight unique repositories")
    if any(not repository.startswith("advanced-fabrics/") for repository in repositories):
        raise ValueError("unexpected repository outside the reviewed public rollout")

    if record["privateInternal"]["activation"] != "blocked-pending-cost-preview":
        raise ValueError("private/internal paid activation must remain blocked")
    if record["aiDetection"] != "excluded":
        raise ValueError("AI detection must remain excluded")
    if record["dependabot"]["securityUpdatePullRequests"] != "opt-in-only":
        raise ValueError("Dependabot update pull requests must remain opt-in")
    if record["evidenceSafety"] != {
        "recordAlertDetails": False,
        "recordSecretValues": False,
        "enablePaidProducts": False,
    }:
        raise ValueError("evidence safety boundary changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "profile",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("static-first-profile.json"),
    )
    args = parser.parse_args()
    validate(json.loads(args.profile.read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
