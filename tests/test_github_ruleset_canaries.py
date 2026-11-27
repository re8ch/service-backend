import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
RULESETS = ROOT / "operations/github-security-governance/rulesets"
SPEC = importlib.util.spec_from_file_location("canary", RULESETS / "canary.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def load(name):
    return json.loads((RULESETS / name).read_text())


def test_canaries_are_evaluate_only_and_have_immutable_check_evidence():
    data = load("canaries.json")
    assert MODULE.validate(data) == []
    assert len(data["canaries"]) == 2
    for canary in data["canaries"]:
        evidence = canary["requiredCheckEvidence"]
        assert len(evidence["headSha"]) == 40
        assert evidence["check"] == "render"
        assert evidence["conclusion"] == "success"
        assert canary["ruleset"]["enforcement"] == "evaluate"
        assert canary["ruleset"]["bypass_actors"] == []


def test_tier_three_inventory_covers_canaries_and_pending_repositories():
    inventory = load("tier-3-inventory.json")
    canaries = load("canaries.json")
    by_repository = {entry["repository"]: entry for entry in inventory["repositories"]}
    assert len(by_repository) == 7
    for canary in canaries["canaries"]:
        assert by_repository[canary["repository"]]["canary"] is True
        assert by_repository[canary["repository"]]["provenChecks"] == ["render"]
    for repository in canaries["pendingAfterCanary"]:
        assert by_repository[repository]["admission"] == "missing"


def test_bypass_inventory_contains_no_personal_user_and_flags_broad_always_modes():
    inventory = load("bypass-inventory.json")
    assert inventory["standingBypassCount"] == len(inventory["entries"])
    assert inventory["personalUserBypassCount"] == 0
    risky = [entry for entry in inventory["entries"] if entry["risk"] == "high"]
    assert {entry["actor"] for entry in risky} == {
        "repository-role:5",
        "enterprise-owner",
    }
