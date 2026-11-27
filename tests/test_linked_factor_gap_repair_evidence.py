import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "operations/github-transaction-context/migrations/linked-factor-gap-repair-2026-11-26.json"
REFERENCE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$")


def evidence():
    return json.loads(EVIDENCE.read_text())


def test_repair_targets_are_unique_and_machine_readable():
    data = evidence()
    pull_requests = [target["pullRequest"] for target in data["targets"]]
    factors = [target["primaryFactor"] for target in data["targets"]]
    assert len(pull_requests) == len(set(pull_requests)) == 10
    assert all(REFERENCE.fullmatch(value) for value in pull_requests + factors)


def test_readback_closes_factor_context_gaps_only():
    data = evidence()
    assert data["scope"]["factorContextIncompleteBefore"] == len(data["targets"])
    assert data["scope"]["factorContextIncompleteAfter"] == 0
    assert data["readback"]["factorContextIncomplete"] == 0
    assert data["scope"]["branchIdentityRepairIncluded"] is False


def test_repair_did_not_mutate_pull_request_lifecycle():
    invariants = evidence()["invariants"]
    assert invariants == {
        "existingValuesPreserved": True,
        "pullRequestBodiesChanged": False,
        "pullRequestsClosed": False,
        "branchesRenamed": False,
    }


def test_compocv_project_received_minimum_context_schema():
    changes = evidence()["projectSchemaChanges"]
    assert changes == [{
        "project": "compocv/1",
        "addedFields": [
            "Lifecycle", "Work Type", "Context Status",
            "Start Date", "Target Date", "Completed Date",
        ],
    }]
