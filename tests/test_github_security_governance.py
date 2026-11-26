import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
GOVERNANCE = ROOT / "operations/github-security-governance"


def baseline():
    return json.loads((GOVERNANCE / "BASELINE-2026-11-27.json").read_text())


def test_baseline_scope_and_read_only_safety_are_explicit():
    record = baseline()
    scope = record["scope"]
    assert scope["repositories"] == (
        scope["activeRepositories"] + scope["archivedRepositories"]
    )
    assert scope["repositories"] == (
        scope["publicRepositories"]
        + scope["privateRepositories"]
        + scope["internalRepositories"]
    )
    assert set(record["safety"].values()) == {0}


def test_policy_requires_evaluation_and_effective_state_readback():
    policy = (GOVERNANCE / "POLICY.md").read_text()
    assert "start in `evaluate`" in policy
    assert "effective state is read back" in policy
    assert "Personal users and broad roles are not standing bypass actors" in policy
    assert "GitHub-hosted runners are the default" in policy


def test_detailed_live_inventory_is_not_committed():
    assert not (GOVERNANCE / "re8ch-enterprise-security.json").exists()
