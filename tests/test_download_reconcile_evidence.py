import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('download_reconcile_evidence', Path(__file__).parents[1] / 'operations/github-maintenance/download_reconcile_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_latest_attempt_wins_even_when_ids_and_api_order_do_not():
    old = {'id': 99, 'name': 'main-reconcile-a', 'created_at': '2026-11-06T01:00:00Z'}
    new = {**old, 'id': 10, 'created_at': '2026-11-06T02:00:00Z'}
    untouched = {'id': 5, 'name': 'main-reconcile-b', 'created_at': '2026-11-06T00:00:00Z'}
    other = {'id': 6, 'name': 'build', 'created_at': '2026-11-06T03:00:00Z'}
    selected = module.latest_artifacts([{'artifacts': [old, untouched, other]}, {'artifacts': [new]}])
    assert {a['id'] for a in selected} == {10, 5}


def test_expired_latest_evidence_does_not_fall_back_to_old_success():
    old = {'id': 1, 'name': 'main-domain-release-result', 'created_at': '2026-11-06T01:00:00Z'}
    new = {**old, 'id': 2, 'created_at': '2026-11-06T02:00:00Z', 'expired': True}
    assert module.latest_artifacts([{'artifacts': [new, old]}]) == [new]


def test_separate_observation_and_failure_artifacts_are_not_release_reports():
    names = ['runtime-observation-reconcile-0-123', 'workflow-failure-reconcile-123-1', 'main-domain-reconcile-example-123']
    artifacts = [{'id': i, 'name': n, 'created_at': '2026-11-06T01:00:00Z'} for i, n in enumerate(names)]
    assert [a['name'] for a in module.latest_artifacts([{'artifacts': artifacts}])] == [names[-1]]
