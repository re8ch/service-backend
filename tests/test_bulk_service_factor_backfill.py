import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/github-transaction-context/bulk_service_backfill.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("bulk_service_backfill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
POLICY = json.loads((SCRIPT.parent / "automation-factor-policy.json").read_text())


def pr(number, author, head, title="change"):
    return {
        "number": number,
        "title": title,
        "body": "original",
        "html_url": f"https://github.com/re8ch/service-backend/pull/{number}",
        "user": {"login": author},
        "head": {"ref": head},
    }


def test_plan_batches_reviewed_automation_and_keeps_human_per_pr():
    prs = [
        pr(1, "dependabot[bot]", "dependabot/npm_and_yarn/a"),
        pr(2, "dependabot[bot]", "dependabot/npm_and_yarn/b"),
        pr(3, "Woomou", "fix/work"),
    ]
    planned = MODULE.plan(prs, {1: [], 2: [], 3: []}, POLICY)
    assert list(planned["automation"].values()) == [[prs[0], prs[1]]]
    assert planned["human"] == [prs[2]]


def test_plan_preserves_linked_and_rejects_unknown_bot():
    prs = [
        pr(1, "Woomou", "feat/work"),
        pr(2, "other[bot]", "dependabot/npm_and_yarn/x"),
    ]
    planned = MODULE.plan(prs, {1: [99], 2: []}, POLICY)
    assert planned["alreadyLinked"] == [1]
    assert planned["exceptions"][0]["number"] == 2


def test_inferred_type_uses_conservative_mapping():
    assert MODULE.inferred_type("fix(api): restore") == ("Bug", "Bug")
    assert MODULE.inferred_type("feat: add") == ("Feature", "Feature")
    assert MODULE.inferred_type("docs: explain") == ("Task", "Task")


def test_append_preserves_body(monkeypatch):
    calls = []
    monkeypatch.setattr(MODULE, "api", lambda token, method, path, body=None: calls.append((method, path, body)))
    row = pr(5, "Woomou", "fix/5")
    MODULE.append_closing_link("token", row, 100)
    assert calls[0][2]["body"].startswith("original\n\n")
    assert calls[0][2]["body"].endswith("Closes #100\n")


def test_verify_requires_native_link_and_open_state(monkeypatch):
    monkeypatch.setattr(MODULE, "list_open_prs", lambda token: [pr(1, "Woomou", "x")])
    monkeypatch.setattr(MODULE, "closing_issue_numbers", lambda token, number: [10])
    readback = MODULE.verify("token", {"transactions": [{"pr": 1, "factor": 10}]})
    assert len(readback["verified"]) == 1
    assert readback["failed"] == []
