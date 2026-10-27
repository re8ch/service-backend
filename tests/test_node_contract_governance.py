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
    assert "immediate/taints" not in body
    assert "Expected nodes" not in body
    assert "Observed nodes" not in body
    assert "encrypted before upload" in body
    assert "sensitive-contract-ref" not in body


def test_sealed_evidence_roundtrip_tamper_and_provenance(tmp_path, monkeypatch):
    import json
    import pytest
    from cryptography.fernet import Fernet, InvalidToken
    monkeypatch.setenv("NODE_EVIDENCE_KEY", Fernet.generate_key().decode())
    payload = {"schemaVersion": "v1alpha1", **evidence(drift("private-node-ref"))}
    source = tmp_path / "evidence.json"
    target = tmp_path / "evidence.fernet"
    source.write_text(json.dumps(payload))
    MODULE.seal_evidence(source, target, "123")
    assert b"private-node-ref" not in target.read_bytes()
    assert MODULE.load_evidence(target, "123") == payload
    with pytest.raises(ValueError, match="provenance"):
        MODULE.load_evidence(target, "another-run")
    with pytest.raises(ValueError, match="provenance"):
        MODULE.load_evidence(target, "123", "2")
    monkeypatch.setattr(MODULE, "REPO", "another/repository")
    with pytest.raises(ValueError, match="provenance"):
        MODULE.load_evidence(target, "123")
    monkeypatch.setattr(MODULE, "REPO", "re8ch/service-backend")
    token = bytearray(target.read_bytes())
    token[len(token)//2] ^= 1
    target.write_bytes(token)
    with pytest.raises(InvalidToken):
        MODULE.load_evidence(target, "123")


def test_sealed_size_does_not_reveal_node_count_and_old_key_fails(tmp_path, monkeypatch):
    import json
    import pytest
    from cryptography.fernet import Fernet, InvalidToken
    monkeypatch.setenv("NODE_EVIDENCE_KEY", Fernet.generate_key().decode())
    sizes = []
    for count in (0, 100):
        source = tmp_path / f"{count}.json"
        target = tmp_path / f"{count}.fernet"
        source.write_text(json.dumps({"schemaVersion": "v1alpha1", **evidence(*[drift(str(i)) for i in range(count)])}))
        MODULE.seal_evidence(source, target, "123")
        sizes.append(target.stat().st_size)
    assert sizes[0] == sizes[1]
    monkeypatch.setenv("NODE_EVIDENCE_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        MODULE.load_evidence(target, "123")


def test_public_workflow_never_uploads_plaintext_evidence():
    import yaml
    workflow = yaml.safe_load((MODULE_PATH.parents[2] / ".github/workflows/node-contract-observer.yml").read_text())
    observe = workflow["jobs"]["observe"]
    uploads = [s for s in observe["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == "${{ runner.temp }}/node-contract-evidence.fernet"
    assert "if" not in uploads[0]  # Failed encryption cannot upload an older artifact.
    assert workflow["jobs"]["governance"]["environment"] == "cluster-observation"
