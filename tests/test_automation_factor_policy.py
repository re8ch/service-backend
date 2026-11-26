import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "operations/github-transaction-context/classify_automation_factor.py"
SPEC = importlib.util.spec_from_file_location("automation_factor", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
POLICY = json.loads((SCRIPT.parent / "automation-factor-policy.json").read_text())


def classify(author, head, title="Bump a dependency", repository="example/repo"):
    return MODULE.classification({
        "repository": repository,
        "number": 7,
        "author": author,
        "headRefName": head,
        "title": title,
    }, POLICY)


def test_dependabot_factor_is_bounded_by_repository_and_ecosystem():
    result = classify("app/dependabot", "dependabot/npm_and_yarn/react-20")
    assert result["decision"] == "aggregate"
    assert result["primaryFactorKey"] == "dependency-update:example/repo:npm_and_yarn"


def test_dependabot_identity_without_standard_branch_fails_closed():
    result = classify("app/dependabot", "feature/not-dependabot")
    assert result["decision"] == "review"


def test_spoofed_dependabot_name_fails_closed():
    result = classify("dependabot-copy[bot]", "dependabot/npm_and_yarn/react-20")
    assert result["decision"] == "review"


def test_domain_promoter_uses_one_repository_stream():
    result = classify(
        "app/re8ch-domain-promoter",
        "codex/promote-domain-45264816351",
        "chore(gitops): promote attested domain OCI digests",
        "re8ch/service-backend",
    )
    assert result["decision"] == "aggregate"
    assert result["primaryFactorKey"] == "domain-oci-promotion:re8ch/service-backend"


def test_domain_promoter_requires_reviewed_title_and_branch():
    result = classify(
        "app/re8ch-domain-promoter",
        "codex/promote-domain-1",
        "chore(gitops): run arbitrary mutation",
    )
    assert result["decision"] == "review"


def test_unknown_automation_requires_review():
    result = classify("app/unknown", "automation/update")
    assert result["actorKind"] == "unknown-automation"
    assert result["primaryFactorKey"] is None


def test_incomplete_ledger_row_requires_review():
    result = MODULE.classification({"author": "app/dependabot"}, POLICY)
    assert result["decision"] == "review"
    assert "lacks" in result["reason"]
