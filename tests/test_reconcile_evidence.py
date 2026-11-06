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


def test_scheduled_ssa_application_is_recorded_as_reconcile(tmp_path):
    report(tmp_path)
    p = tmp_path / 'result.json'
    value = json.loads(p.read_text())
    value['application'] = {'appliedInventory': ['apps/Deployment/demo/service']}
    p.write_text(json.dumps(value))
    assert module.summarize(tmp_path, 'success')['action'] == 'Reconcile'


def test_authorized_node_warning_stays_visible_in_project(tmp_path):
    report(tmp_path)
    p = tmp_path / 'result.json'
    value = json.loads(p.read_text())
    value['health']['reason'] = 'Warning: NotReady nodes offline; Ready-node daemon Pods healthy'
    p.write_text(json.dumps(value))
    evidence = module.summarize(tmp_path, 'success')
    assert evidence['state'] == 'Degraded'
    assert evidence['reports'][0]['warning'].startswith('Warning:')


def test_applied_result_uses_separate_health_observation(tmp_path):
    report(tmp_path, 'applied', 'degraded')
    assert module.summarize(tmp_path, 'success')['state'] == 'Degraded'
    report(tmp_path, 'applied', 'healthy')
    assert module.summarize(tmp_path, 'success')['state'] == 'Healthy'
