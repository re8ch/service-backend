#!/usr/bin/env python3
"""Build and validate the owned-repository transaction-context inventory."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEVELOPER_CONTEXT = ROOT / "indexes/developer-context.json"
GITOPS_REPOSITORIES = ROOT / "clusters/re8ch/components/gitops-events/repositories.json"
INVENTORY = Path(__file__).with_name("repository-inventory.json")

OWNED_ORGANIZATIONS = {
    "8iops", "Woomou", "advanced-fabrics", "among-clusters",
    "bifurcate-systems", "biziops", "compocv", "pillars-infra",
    "public-edge", "re8ch", "re8ch-ai", "re8ch-business", "re8ch-cloud",
    "re8ch-image", "re8ch-observability", "siren-observer", "strata-silica",
}
REPOSITORY_URL = re.compile(
    r"^(?:ssh://git@ssh\.github\.com:443/|https://github\.com/|git@github\.com:)([^/]+/[^/]+?)\.git$"
)
RELATIONSHIP_STATES = {"complete", "partial", "missing", "blocked"}
ENFORCEMENT_STATES = {"required", "available", "not-installed", "blocked"}
CAPABILITY_STATES = {"available", "missing", "blocked"}
GITOPS_ADOPTED = {
    "8iops/agent-gitops": "8iops/agent-gitops#329",
    "pillars-infra/network-gitops": "pillars-infra/network-gitops#320",
    "re8ch/identity-gitops": "re8ch/identity-gitops#99",
    "siren-observer/observability-gitops": "siren-observer/observability-gitops#114",
    "strata-silica/storage-gitops": "strata-silica/storage-gitops#61",
}


def strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def catalog_repositories() -> set[str]:
    context = json.loads(DEVELOPER_CONTEXT.read_text())
    repositories = set()
    for value in strings(context):
        match = REPOSITORY_URL.match(value)
        if match and match.group(1).split("/", 1)[0] in OWNED_ORGANIZATIONS:
            repositories.add(match.group(1))

    gitops = json.loads(GITOPS_REPOSITORIES.read_text())
    repositories.update(gitops.values())
    return repositories


def build_inventory() -> dict:
    entries = []
    for repository in sorted(catalog_repositories()):
        entry = {
            "repository": repository,
            "localAuthority": "gap: repository authority not yet audited",
            "approvedProject": "gap: approved Project and fallback not yet declared",
            "relationships": "missing",
            "enforcement": "not-installed",
            "capabilities": {
                "issueContext": "missing",
                "pullRequestContext": "missing",
                "projectContext": "missing",
                "immutableRevision": "missing",
                "runtimeEvidenceLink": "missing",
            },
            "evidence": "catalog membership only; live repository audit pending",
        }
        if repository == "re8ch/service-backend":
            entry.update({
                "localAuthority": "AGENTS.md and operations/github-transaction-context/README.md",
                "approvedProject": "re8ch Project 2; fallback: Others",
                "relationships": "complete",
                "enforcement": "required",
                "capabilities": {
                    "issueContext": "available",
                    "pullRequestContext": "available",
                    "projectContext": "available",
                    "immutableRevision": "available",
                    "runtimeEvidenceLink": "available",
                },
                "evidence": "re8ch/service-backend#3096, #3101; Ruleset 23148035 readback",
            })
        elif repository in GITOPS_ADOPTED:
            entry.update({
                "localAuthority": "repository AGENTS.md and .github creation scaffolding",
                "approvedProject": "declared by repository-local Issue/PR context",
                "relationships": "partial",
                "enforcement": "blocked",
                "capabilities": {
                    "issueContext": "available",
                    "pullRequestContext": "available",
                    "projectContext": "available",
                    "immutableRevision": "available",
                    "runtimeEvidenceLink": "available",
                },
                "evidence": (
                    f"{GITOPS_ADOPTED[repository]}; required check waits for approved "
                    "cross-organization Project-read identity"
                ),
            })
        entries.append(entry)
    return {
        "apiVersion": "governance.re8ch.com/v1alpha1",
        "kind": "RepositoryTransactionContextInventory",
        "source": {
            "catalog": "indexes/developer-context.json",
            "gitopsAuthorities": "clusters/re8ch/components/gitops-events/repositories.json",
        },
        "semantics": "minimum machine-readable relationships; presentation remains repository-local",
        "developerWorkflow": {
            "entryPoint": "create or select one Issue",
            "humanInputs": [
                "observable outcome",
                "ownership only when automation cannot resolve it",
                "acceptance decision and runtime evidence",
            ],
            "automationOwns": [
                "Project enrollment and default fields",
                "branch and pull-request Issue identity",
                "closing relationship and status transitions",
                "check, revision and evidence back-links",
            ],
            "inventoryAudience": "governance automation and maintainers; not a developer checklist",
        },
        "repositories": entries,
    }


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    workflow = data.get("developerWorkflow", {})
    if workflow.get("entryPoint") != "create or select one Issue":
        errors.append("developer workflow must have one Issue entry point")
    if not workflow.get("automationOwns"):
        errors.append("automation responsibilities must be explicit")
    entries = data.get("repositories", [])
    names = [entry.get("repository") for entry in entries]
    expected = catalog_repositories()
    actual = set(names)
    if len(names) != len(actual):
        errors.append("repository identities must be unique")
    if missing := sorted(expected - actual):
        errors.append(f"catalog repositories missing: {', '.join(missing)}")
    if extra := sorted(actual - expected):
        errors.append(f"non-catalog repositories present: {', '.join(extra)}")

    for entry in entries:
        name = entry.get("repository", "<unknown>")
        if entry.get("relationships") not in RELATIONSHIP_STATES:
            errors.append(f"{name}: unsupported relationships state")
        if entry.get("enforcement") not in ENFORCEMENT_STATES:
            errors.append(f"{name}: unsupported enforcement state")
        capabilities = entry.get("capabilities", {})
        expected_capabilities = {
            "issueContext", "pullRequestContext", "projectContext",
            "immutableRevision", "runtimeEvidenceLink",
        }
        if set(capabilities) != expected_capabilities:
            errors.append(f"{name}: capability keys must be complete")
        for capability, state in capabilities.items():
            if state not in CAPABILITY_STATES:
                errors.append(f"{name}: unsupported {capability} capability state")
        if not entry.get("localAuthority"):
            errors.append(f"{name}: localAuthority must be explicit")
        if not entry.get("approvedProject"):
            errors.append(f"{name}: approvedProject or an explicit gap is required")
        if not entry.get("evidence"):
            errors.append(f"{name}: evidence must be explicit")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        args.inventory.write_text(json.dumps(build_inventory(), indent=2) + "\n")
    errors = validate(json.loads(args.inventory.read_text()))
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
