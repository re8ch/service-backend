import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/github-transaction-context/bulk_backfill_human_factors.py"
SPEC = importlib.util.spec_from_file_location("bulk_human_factor_backfill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def pr(**overrides):
    value = {
        "repository": "example/repo", "number": 7, "title": "Add useful thing",
        "url": "https://github.com/example/repo/pull/7", "detached": True,
        "createdAt": "2026-01-01T00:00:00Z",
        "author": {"kind": "human", "login": "developer"},
    }
    value.update(overrides)
    return value


def test_targets_exclude_automation_linked_and_service_backend():
    ledger = {"pullRequests": [
        pr(), pr(number=8, author={"kind": "automation", "login": "bot"}),
        pr(number=9, detached=False), pr(number=10, repository="re8ch/service-backend"),
    ]}
    assert [item["number"] for item in MODULE.targets(ledger)] == [7]


def test_factor_body_preserves_source_identity_and_root():
    body = MODULE.factor_body(pr())
    assert "Parent Issue: none" in body
    assert "example/repo#7" in body
    assert MODULE.marker(pr()) in body


def test_closing_reference_preserves_original_body_and_is_idempotent():
    original = "Original details"
    updated = MODULE.append_closing_reference(original, 42, pr())
    assert updated.startswith(original)
    assert "Closes re8ch/service-backend#42" in updated
    assert MODULE.append_closing_reference(updated, 42, pr()) == updated


def test_issue_type_mapping():
    assert MODULE.issue_type(pr(title="fix: broken path")) == "Bug"
    assert MODULE.issue_type(pr(title="Add useful thing")) == "Feature"
    assert MODULE.issue_type(pr(title="Document behavior")) == "Task"


def test_canonical_repository_comes_from_live_pr_url():
    moved = pr(repository="old-owner/old-repo", url="https://github.com/new-owner/new-repo/pull/7")
    assert MODULE.canonical_repository(moved) == "new-owner/new-repo"


def test_bulk_migration_record_accounts_for_every_planned_pr():
    record = __import__("json").loads(
        (SCRIPT.parent / "migrations/2026-11-26-bulk-human-nonservice.json").read_text()
    )
    assert record["counts"]["planned"] == 79
    assert record["counts"]["nativeClosingLinked"] + record["counts"]["exceptions"] == 79
    assert len(record["transactions"]) == 77
    assert {item["result"] for item in record["exceptions"]} == {
        "non-default-base-no-native-closing-reference"
    }
