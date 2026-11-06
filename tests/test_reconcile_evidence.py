import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('reconcile_evidence', Path(__file__).parents[1] / 'operations/github-maintenance/reconcile_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def report(tmp_path, outcome='healthy', health='healthy'):
    (tmp_path / 'result.json').write_text(json.dumps({'releaseId': 'example', 'approvedDigest': 'sha256:abc', 'outcome': outcome, 'health': {'status': health}}))


def test_success_with_degraded_runtime_is_not_healthy(tmp_path):
    report(tmp_path, 'degraded', 'degraded')
    assert module.summarize(tmp_path, 'success')['state'] == 'Degraded'


def test_failure_overrides_an_older_healthy_artifact(tmp_path):
    report(tmp_path)
    assert module.summarize(tmp_path, 'failure')['state'] == 'Failed'


def test_missing_artifacts_cannot_be_healthy(tmp_path):
    assert module.summarize(tmp_path, 'success')['state'] == 'Unknown'


def test_malformed_artifact_cannot_be_healthy(tmp_path):
    (tmp_path / 'result.json').write_text('')
    assert module.summarize(tmp_path, 'success')['state'] == 'Unknown'


def test_rollback_does_not_mean_target_recovered(tmp_path):
    report(tmp_path, 'rolled-back')
    assert module.summarize(tmp_path, 'success')['state'] == 'Failed'


def test_runtime_health_and_workflow_must_both_pass(tmp_path):
    report(tmp_path)
    assert module.summarize(tmp_path, 'success')['state'] == 'Healthy'


def test_wrong_delivery_is_not_reused(tmp_path):
    (tmp_path / 'result.json').write_text(json.dumps({'release': 'example', 'digest': 'sha256:abc', 'delivery': 'old', 'outcome': 'aligned'}))
    assert module.summarize(tmp_path, 'success', 'new')['state'] == 'Unknown'
