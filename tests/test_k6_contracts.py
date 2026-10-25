import copy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("k6_contract_validator", Path(__file__).resolve().parents[1] / "operations/k6/validate.py")
k6 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(k6)


def catalog():
    return k6.read_json(k6.DEFAULT_CATALOG)


def test_live_schedule_matches_reviewed_cadences():
    k6.validate_catalog(catalog())
    k6.validate_schedules(catalog(), k6.DEFAULT_K8S, k6.DEFAULT_WORKFLOW)


def test_schedule_drift_is_rejected():
    changed = copy.deepcopy(catalog())
    changed["cadences"]["fast"]["internalCron"] = "*/5 * * * *"
    with pytest.raises(AssertionError, match="internal schedule drift"):
        k6.validate_schedules(changed, k6.DEFAULT_K8S, k6.DEFAULT_WORKFLOW)


def test_external_probe_cannot_target_service_dns():
    changed = copy.deepcopy(catalog())
    changed["probes"][0]["url"] = "https://service.auth.svc.cluster.local/healthz"
    with pytest.raises(AssertionError, match="public HTTPS"):
        k6.validate_catalog(changed)


def test_contract_revision_digest_detects_dataset_edit():
    raw = k6.repo_path(k6.DEFAULT_CATALOG).read_bytes()
    revisions = k6.read_json(k6.DEFAULT_REVISIONS)["revisions"]
    k6.validate_revisions(raw, revisions)
    changed = json.dumps({**json.loads(raw), "probes": []}).encode()
    with pytest.raises(AssertionError, match="digest"):
        k6.validate_revisions(changed, revisions)


def test_pr_gate_is_offline_while_production_monitor_keeps_failure_signal():
    import yaml
    root = Path(__file__).resolve().parents[1]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    job = ci["jobs"]["k6-contracts"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "operations/k6/validate.py" in commands
    assert "inspect smoke.js" in commands and "--network none" in commands
    assert "run smoke.js" not in commands
    assert "k6-contracts" in ci["jobs"]["control-plane-validate"]["needs"]
    monitor = yaml.safe_load((root / ".github/workflows/k6-contracts.yml").read_text())
    live = monitor["jobs"]["contracts"]
    assert not live.get("continue-on-error", False)
    probes = [s for s in live["steps"] if "run --summary-export" in s.get("run", "")]
    assert len(probes) == 1 and not probes[0].get("continue-on-error", False)
