import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/github-transaction-context/factor_audit.py"
SPEC = importlib.util.spec_from_file_location("transaction_factor_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MODEL = json.loads(MODULE.MODEL.read_text())


def project(ref="re8ch/2"):
    return {"ref": ref, "fields": {
        "Status": "In Progress", "Lifecycle": "In Progress",
        "Work Type": "Governance", "Context Status": "Ready",
        "Start Date": "2026-11-26", "Target Date": "2026-12-10",
    }}


def primary(ref="re8ch/service-backend#3114"):
    return {
        "ref": ref, "role": "primary", "relationship": "closes",
        "issueType": "Task",
        "parent": {"kind": "issue", "ref": "re8ch/service-backend#3112"},
        "projects": [project()],
    }


def record():
    return {
        "repository": "re8ch/service-backend", "number": 3115,
        "authorKind": "human", "headRefName": "codex/3114-human-crossorg-audit",
        "factors": [primary()],
    }


def test_complete_primary_factor_passes():
    assert MODULE.audit(record(), MODEL) == []


def test_detached_human_pr_fails():
    data = record()
    data["factors"] = []
    assert "exactly one primary factor" in MODULE.audit(data, MODEL)[0]


def test_cross_organization_secondary_factor_is_valid():
    data = record()
    data["factors"].append({
        "ref": "pillars-infra/network-gitops#227", "role": "secondary",
        "relationship": "depends_on", "projects": [project("pillars-infra/1")],
    })
    assert MODULE.audit(data, MODEL) == []


def test_cross_organization_primary_is_valid_when_it_owns_lifecycle():
    data = record()
    data["headRefName"] = "feature/227-network-dependency"
    data["factors"] = [primary("pillars-infra/network-gitops#227")]
    data["factors"][0]["projects"] = [project("pillars-infra/1")]
    assert MODULE.audit(data, MODEL) == []


def test_incomplete_project_and_missing_parent_fail():
    data = record()
    data["factors"][0]["projects"] = [{"ref": "re8ch/2", "fields": {"Status": "Todo"}}]
    data["factors"][0]["parent"] = {}
    failures = MODULE.audit(data, MODEL)
    assert any("parent Issue or explicit root" in failure for failure in failures)
    assert any("complete lifecycle Project" in failure for failure in failures)


def test_duplicate_factor_reference_fails():
    data = record()
    data["factors"].append({**primary(), "role": "secondary", "relationship": "observes"})
    assert any("must be unique" in failure for failure in MODULE.audit(data, MODEL))


def test_automation_records_are_owned_by_separate_policy():
    data = record()
    data["authorKind"] = "automation"
    data["factors"] = []
    assert MODULE.audit(data, MODEL) == []
