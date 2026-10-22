import datetime as dt
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "operations/github-maintenance/node_contract_governance.py"
SPEC = importlib.util.spec_from_file_location("node_contract_governance", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def drift(key: str, severity: str = "persistent", kind: str = "label") -> dict:
    return {"type": kind, "contractRef": key, "fieldRef": "field", "severity": severity}


def evidence(*items: dict) -> dict:
    return {"drift": list(items), "expectedNodeCount": 14, "observedNodeCount": 14}


def test_transport_failures_do_not_count_as_drift_history():
    current = evidence(drift("same"))
    assert MODULE.persistent_keys(current, []) == set()


def test_only_same_key_across_three_valid_observations_is_eligible():
    now = dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc)
    history = [
        (now - dt.timedelta(hours=1), evidence(drift("same"), drift("old-only"))),
        (now - dt.timedelta(hours=2), evidence(drift("same"), drift("different"))),
    ]
    assert MODULE.persistent_keys(evidence(drift("same"), drift("new")), history, now=now) == {
        "label:same:field"
    }


def test_continuous_24_hour_drift_is_eligible():
    now = dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc)
    history = [(now - dt.timedelta(hours=25), evidence(drift("same")))]
    assert MODULE.persistent_keys(evidence(drift("same")), history, now=now) == {
        "label:same:field"
    }


def test_issue_body_contains_only_aggregate_details(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    body = MODULE.render_body(
        [drift("sensitive-contract-ref", "immediate", "taints")],
        evidence(drift("sensitive-contract-ref", "immediate", "taints")),
    )
    assert "immediate/taints" in body
    assert "sensitive-contract-ref" not in body
