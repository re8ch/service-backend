import datetime as dt
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/github-transaction-context/intake.py"
SPEC = importlib.util.spec_from_file_location("transaction_context_intake", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_issue_form_work_type_maps_to_project_and_native_type():
    body = "### Work type\n\nGovernance\n\n### Outcome\n\nAutomate it"
    assert MODULE.work_type(body) == "Governance"
    assert MODULE.native_issue_type("Governance") == "Task"
    assert MODULE.native_issue_type("Bug") == "Bug"


def test_unknown_work_type_uses_safe_task_default():
    assert MODULE.work_type("### Work type\n\nUnknown") == "Task"


def test_defaults_are_deterministic_and_reviewable():
    values = MODULE.desired_fields("### Work type\n\nFeature", dt.date(2026, 11, 26))
    assert values == {
        "Status": "Todo",
        "Lifecycle": "Ready",
        "Context Status": "Ready",
        "Work Type": "Feature",
        "Start Date": "2026-11-26",
        "Target Date": "2026-12-10",
    }


def test_next_action_has_one_branch_and_closing_reference():
    comment = MODULE.next_action({"number": 3105, "title": "Automate Issue defaults"})
    assert "codex/3105-automate-issue-defaults" in comment
    assert "Closes #3105" in comment
    assert MODULE.MARKER in comment


def test_branch_slug_is_bounded():
    branch = MODULE.branch_name(1, "A" * 100)
    assert branch.startswith("codex/1-")
    assert len(branch.split("/", 1)[1].split("-", 1)[1]) == 48


def graph_data(item=True, marker=True):
    options = {
        "Status": ["Todo"],
        "Lifecycle": ["Ready"],
        "Context Status": ["Ready"],
        "Work Type": ["Task", "Bug"],
    }
    fields = []
    for name, values in options.items():
        fields.append({"id": f"field-{name}", "name": name, "options": [{"id": f"option-{value}", "name": value} for value in values]})
    fields.extend([
        {"id": "field-start", "name": "Start Date", "dataType": "DATE"},
        {"id": "field-target", "name": "Target Date", "dataType": "DATE"},
    ])
    return {
        "repository": {
            "issue": {
                "id": "issue", "number": 3105, "title": "Work", "body": "",
                "issueType": {"name": "Task"},
                "comments": {"nodes": [{"body": MODULE.MARKER}] if marker else []},
                "projectItems": {"nodes": [{
                    "id": "item",
                    "project": {"id": "project", "number": 2, "owner": {"login": "re8ch"}},
                    "fieldValues": {"nodes": []},
                }] if item else []},
            },
            "issueTypes": {"nodes": [{"id": "type-task", "name": "Task"}]},
        },
        "organization": {"projectV2": {"id": "project", "fields": {"nodes": fields}}},
    }


def test_reconcile_reuses_existing_item_and_comment(monkeypatch):
    calls = []
    monkeypatch.setattr(MODULE, "request", lambda *_args, **_kwargs: graph_data())
    monkeypatch.setattr(MODULE, "mutation", lambda _token, query, **variables: calls.append((query, variables)) or {})
    MODULE.reconcile("token", "re8ch/service-backend", 3105, dt.date(2026, 11, 26))
    assert not any("addProjectV2ItemById" in query for query, _ in calls)
    assert not any("addComment" in query for query, _ in calls)
    assert sum("updateProjectV2ItemFieldValue" in query for query, _ in calls) == 6


def test_reconcile_creates_missing_item_and_comment(monkeypatch):
    calls = []
    monkeypatch.setattr(MODULE, "request", lambda *_args, **_kwargs: graph_data(item=False, marker=False))

    def mutate(_token, query, **variables):
        calls.append((query, variables))
        if "addProjectV2ItemById" in query:
            return {"addProjectV2ItemById": {"item": {"id": "new-item"}}}
        return {}

    monkeypatch.setattr(MODULE, "mutation", mutate)
    MODULE.reconcile("token", "re8ch/service-backend", 3105, dt.date(2026, 11, 26))
    assert any("addProjectV2ItemById" in query for query, _ in calls)
    assert any("addComment" in query for query, _ in calls)


def test_reconcile_preserves_existing_project_values(monkeypatch):
    data = graph_data()
    data["repository"]["issue"]["projectItems"]["nodes"][0]["fieldValues"]["nodes"] = [
        {"name": "In Progress", "field": {"name": "Lifecycle"}},
        {"date": "2027-01-20", "field": {"name": "Target Date"}},
    ]
    calls = []
    monkeypatch.setattr(MODULE, "request", lambda *_args, **_kwargs: data)
    monkeypatch.setattr(MODULE, "mutation", lambda _token, query, **variables: calls.append((query, variables)) or {})
    MODULE.reconcile("token", "re8ch/service-backend", 3105, dt.date(2026, 11, 26))
    updated_fields = {
        variables.get("field")
        for query, variables in calls
        if "updateProjectV2ItemFieldValue" in query
    }
    assert "field-Lifecycle" not in updated_fields
    assert "field-target" not in updated_fields


def test_scan_reconciles_only_unmarked_open_issues(monkeypatch):
    scan_data = {
        "repository": {"issues": {"nodes": [
            {"number": 1, "comments": {"nodes": []}},
            {"number": 2, "comments": {"nodes": [{"body": MODULE.MARKER}]}},
        ]}}
    }
    reconciled = []
    monkeypatch.setattr(MODULE, "request", lambda *_args, **_kwargs: scan_data)
    monkeypatch.setattr(MODULE, "reconcile", lambda _token, _repo, number, *_args: reconciled.append(number))
    result = MODULE.scan("token", "re8ch/service-backend", dt.date(2026, 11, 26), "re8ch", 2)
    assert result == [1]
    assert reconciled == [1]
