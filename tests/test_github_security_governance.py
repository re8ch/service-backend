import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
GOVERNANCE = ROOT / "operations/github-security-governance"


def baseline():
    return json.loads((GOVERNANCE / "BASELINE-2026-11-27.json").read_text())


def security_products_baseline():
    return json.loads(
        (GOVERNANCE / "SECURITY-PRODUCTS-BASELINE-2026-11-27.json").read_text()
    )


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


def test_security_products_keep_detection_remediation_and_cost_separate():
    record = security_products_baseline()
    assert record["dependabot"]["alertsEnabledActiveNonFork"] > record[
        "dependabot"
    ]["securityUpdatesEnabledActiveNonFork"]
    assert record["secretProtection"]["publicGapsActiveNonFork"] == 8
    assert record["secretProtection"]["privateOrInternalGapsActiveNonFork"] == 80
    assert record["safety"] == {
        "secretValuesRecorded": False,
        "alertDetailsRecorded": False,
        "paidProductsEnabled": 0,
        "aiFeaturesEnabled": 0,
        "bulkMutations": 0,
        "liveCanariesApplied": 1,
    }


def test_security_canary_is_free_bounded_and_reversible():
    canary = security_products_baseline()["canary"]
    assert canary["visibility"] == "public"
    assert canary["riskTier"] == 1
    assert canary["plannedMutation"] == [
        "secret_scanning=enabled",
        "secret_scanning_push_protection=enabled",
    ]
    assert "secret_scanning_ai_detection" in canary["excludedMutation"]
    assert canary["rollback"] == [
        "secret_scanning_push_protection=disabled",
        "secret_scanning=disabled",
    ]
    assert canary["status"] == "applied-readback-complete"
    assert canary["readback"]["paidProductsEnabled"] == 0
    assert canary["readback"]["naturalDeveloperPathProbe"] == (
        "pending-next-legitimate-change"
    )


def test_static_first_profile_has_zero_actions_and_compile_cost():
    profile = json.loads((GOVERNANCE / "static-first-profile.json").read_text())
    assert profile["delivery"] == {
        "mode": "github-native-server-side",
        "workflowsAdded": 0,
        "requiredChecksAdded": 0,
        "codeqlSetupsAdded": 0,
        "repositoryBuildMinutesAdded": 0,
    }
    repositories = {
        repository
        for batch in profile["publicRollout"]["batches"]
        for repository in batch["repositories"]
    }
    assert len(repositories) == 8
    assert profile["privateInternal"]["activation"] == (
        "blocked-pending-cost-preview"
    )


def test_static_first_validator_accepts_reviewed_profile():
    path = GOVERNANCE / "validate_static_first.py"
    spec = importlib.util.spec_from_file_location("validate_static_first", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    profile = json.loads((GOVERNANCE / "static-first-profile.json").read_text())
    module.validate(profile)
