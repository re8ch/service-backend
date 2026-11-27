import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
GOVERNANCE = ROOT / "operations/github-security-governance"
BASELINE = GOVERNANCE / "ACTIONS-ENVIRONMENTS-BASELINE-2026-11-27.json"


def load_audit_module():
    path = GOVERNANCE / "audit_workflows.py"
    sys.path.insert(0, str(GOVERNANCE))
    spec = importlib.util.spec_from_file_location("actions_reference_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_classification_distinguishes_immutable_and_local():
    module = load_audit_module()
    assert module.classify("actions/checkout@11d5960a326750d5838078e36cf38b85af677262") == "immutable"
    assert module.classify("actions/checkout@v4") == "mutable"
    assert module.classify("./.github/actions/local") == "local"


def test_baseline_counts_and_canary_are_bounded():
    baseline = json.loads(BASELINE.read_text())
    actions = baseline["actions"]
    assert actions["usesReferences"] == (
        actions["immutableReferences"]
        + actions["localReferences"]
        + actions["mutableReferences"]
    )
    assert baseline["canary"]["workflowConclusion"] == "success"
    assert baseline["canary"]["effectiveStateReadback"] is True
    assert set(baseline["safety"].values()) == {False}


def test_policy_preserves_hosted_default_and_environment_safety():
    policy = (GOVERNANCE / "ACTIONS-ENVIRONMENTS.md").read_text()
    assert "GitHub-hosted runners are the default" in policy
    assert "must not execute untrusted pull-request code" in policy
    assert "neither protection rules nor a deployment branch policy" in policy
    assert "Environment changes use one canary" in policy
