import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/github-transaction-context/validate.py"
SPEC = importlib.util.spec_from_file_location("transaction_context", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONFIG = json.loads((SCRIPT.parent / "config.json").read_text())


def issue(number=3095, values=None):
    values = values or {
        "Status": "In Progress",
        "Lifecycle": "In Progress",
        "Work Type": "Governance",
        "Context Status": "Ready",
        "Start Date": "2026-11-26",
        "Target Date": "2026-11-26",
    }
    nodes = []
    for name, value in values.items():
        key = "date" if "Date" in name else "name"
        nodes.append({"field": {"name": name}, key: value})
    return {
        "number": number,
        "body": "Parent Issue: none",
        "issueType": {"name": "Task"},
        "parent": None,
        "projectItems": {
            "nodes": [{
                "project": {"number": 2, "title": "Internal", "owner": {"login": "re8ch"}},
                "fieldValues": {"nodes": nodes},
            }]
        },
    }


def pull_request(issues=None, author="Woomou", head="governance/3095-context", title="Change context"):
    return {
        "number": 1,
        "author": {"login": author},
        "title": title,
        "headRefName": head,
        "closingIssuesReferences": {"nodes": issues or []},
    }


def test_complete_context_passes():
    assert MODULE.validate(pull_request([issue()]), CONFIG) == []


def test_unlinked_pull_request_fails():
    assert "must close at least one Issue" in MODULE.validate(pull_request(), CONFIG)[0]


def test_missing_project_fields_fail():
    failures = MODULE.validate(pull_request([issue(values={"Status": "Todo"})]), CONFIG)
    assert "Lifecycle" in failures[0]
    assert "Target Date or Completed Date" in failures[0]


def test_issue_number_must_be_in_branch():
    failures = MODULE.validate(pull_request([issue()], head="fix/no-context"), CONFIG)
    assert "branch name" in failures[0]


def test_dependabot_standard_branch_exempts_only_issue_number():
    pr = pull_request([issue()], author="app/dependabot", head="dependabot/npm_and_yarn/vite-7")
    assert MODULE.validate(pr, CONFIG) == []


def test_dependabot_maintenance_app_graphql_alias_is_exactly_supported():
    pr = pull_request([issue()], author="dependabot", head="dependabot/pip/pyyaml-6")
    assert MODULE.validate(pr, CONFIG) == []


def test_dependabot_rest_alias_is_exactly_supported():
    pr = pull_request([issue()], author="dependabot[bot]", head="dependabot/pip/pyyaml-6")
    assert MODULE.validate(pr, CONFIG) == []


def test_dependabot_still_requires_closing_issue():
    pr = pull_request(author="app/dependabot", head="dependabot/npm_and_yarn/vite-7")
    assert "must close at least one Issue" in MODULE.validate(pr, CONFIG)[0]


def test_similar_bot_name_is_not_exempt():
    pr = pull_request([issue()], author="app/dependabot-copy", head="dependabot/npm_and_yarn/vite-7")
    assert MODULE.validate(pr, CONFIG)


def test_domain_promoter_exact_contract_exempts_branch_number():
    pr = pull_request(
        [issue()],
        author="app/re8ch-domain-promoter",
        head="codex/promote-domain-40567205243",
        title="chore(gitops): promote attested domain OCI digests",
    )
    assert MODULE.validate(pr, CONFIG) == []


def test_domain_promoter_maintenance_app_graphql_alias_is_supported():
    pr = pull_request(
        [issue()],
        author="re8ch-domain-promoter",
        head="codex/promote-domain-40567205243",
        title="chore(gitops): promote verified domain OCI digests",
    )
    assert MODULE.validate(pr, CONFIG) == []


def test_domain_promoter_unreviewed_title_fails_closed():
    pr = pull_request(
        [issue()],
        author="app/re8ch-domain-promoter",
        head="codex/promote-domain-40567205243",
        title="change unrelated files",
    )
    assert "branch name" in MODULE.validate(pr, CONFIG)[0]


def test_project_owner_query_uses_union_fragments():
    assert "... on Organization { login }" in MODULE.QUERY
    assert "... on User { login }" in MODULE.QUERY
