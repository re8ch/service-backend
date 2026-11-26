import datetime as dt
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/github-transaction-context/audit_open_prs.py"
SPEC = importlib.util.spec_from_file_location("open_pr_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CONFIG = {
    "requiredFields": ["Status", "Lifecycle", "Work Type", "Context Status", "Start Date"],
    "completionDateFields": ["Target Date", "Completed Date"],
}
AS_OF = dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)


def field(name, value):
    return {"field": {"name": name}, "date" if "Date" in name else "name": value}


def issue(number=12, complete=True):
    values = {
        "Status": "Todo", "Lifecycle": "Ready", "Work Type": "Task",
        "Context Status": "Ready", "Start Date": "2026-11-20",
        "Target Date": "2026-12-04",
    }
    if not complete:
        values.pop("Target Date")
    return {
        "number": number,
        "url": f"https://github.com/acme/repo/issues/{number}",
        "body": "Parent Issue: none",
        "repository": {"nameWithOwner": "acme/repo"},
        "issueType": {"name": "Task"},
        "parent": None,
        "projectItems": {"nodes": [{
            "project": {"number": 1, "title": "Delivery", "url": "p", "owner": {"login": "acme"}},
            "fieldValues": {"nodes": [field(name, value) for name, value in values.items()]},
        }]},
    }


def pr(number=1, issues=None, author=None, head="codex/12-work"):
    return {
        "number": number, "url": f"https://github.com/acme/repo/pull/{number}",
        "title": "Work", "headRefName": head,
        "createdAt": "2026-11-20T00:00:00Z", "updatedAt": "2026-11-25T00:00:00Z",
        "author": author or {"__typename": "User", "login": "dev"},
        "closingIssuesReferences": {"totalCount": len(issues or []), "nodes": issues or []},
    }


def test_detached_pr_is_explicit():
    record = MODULE.pr_record("acme/repo", pr(), AS_OF, CONFIG)
    assert record["detached"] is True
    assert record["factorCount"] == 0
    assert record["ageDays"] == 6


def test_linked_pr_records_branch_and_complete_project():
    record = MODULE.pr_record("acme/repo", pr(issues=[issue()]), AS_OF, CONFIG)
    assert record["detached"] is False
    assert record["branchHasFactorIdentity"] is True
    assert record["hasCompleteProjectFactor"] is True
    assert record["transactionComplete"] is True


def test_multiple_cross_repository_factors_are_preserved():
    second = issue(44)
    second["repository"]["nameWithOwner"] = "other-org/factors"
    record = MODULE.pr_record("acme/repo", pr(issues=[issue(), second]), AS_OF, CONFIG)
    assert record["factorCount"] == 2
    assert record["factors"][1]["repository"] == "other-org/factors"


def test_linked_pr_without_branch_identity_is_not_transaction_complete():
    record = MODULE.pr_record("acme/repo", pr(issues=[issue()], head="feature/no-number"), AS_OF, CONFIG)
    assert record["detached"] is False
    assert record["transactionComplete"] is False


def test_incomplete_project_is_reported_without_rewriting_it():
    record = MODULE.pr_record("acme/repo", pr(issues=[issue(complete=False)]), AS_OF, CONFIG)
    project = record["factors"][0]["projects"][0]
    assert project["complete"] is False
    assert "Target Date or Completed Date" in project["missingFields"]


def test_bot_classification():
    bot = {"__typename": "Bot", "login": "dependabot[bot]"}
    assert MODULE.author_kind(bot) == "automation"
    assert MODULE.author_kind({"__typename": "User", "login": "dev"}) == "human"


def test_repository_pagination():
    pages = iter([
        {"nodes": [pr(1)], "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
        {"nodes": [pr(2, issues=[issue()])], "pageInfo": {"hasNextPage": False, "endCursor": None}},
    ])
    cursors = []

    def fetch(_token, variables):
        cursors.append(variables["cursor"])
        return next(pages)

    records = MODULE.audit_repository("acme/repo", "token", AS_OF, CONFIG, fetch)
    assert [item["number"] for item in records] == [1, 2]
    assert cursors == [None, "next"]


def test_project_details_use_bounded_followup_query():
    assert "projectItems" not in MODULE.PR_QUERY
    assert "projectItems(first: 10)" in MODULE.ISSUE_QUERY
    assert "fieldValues(first: 20)" in MODULE.ISSUE_QUERY
