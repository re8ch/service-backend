import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'operations/migrations/github-spec-machine/reconcile_decision.py'
SPEC = importlib.util.spec_from_file_location('reconcile_decision', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def evidence():
    return dict(cluster='lab', service='a', objectKey='apps/v1/Deployment/a/web',
        uid='uid-1', resourceVersion='opaque-rv', desiredRevision='new-main',
        acceptedRevision='accepted-main', policyRevision='policy-sha',
        desiredInputDigest='same-service-input', acceptedInputDigest='same-service-input',
        acceptedValid=True, acceptedSpecDigest='target', observedSpecDigest='drift',
        observation='fresh', ownership='managed', hold='none', repairMode='automatic',
        changeClass='expected', runtime='healthy')


def test_plan_binds_object_and_revisions_without_authorizing(evidence):
    plan = MODULE.decide(evidence)
    assert plan['action'] == 'plan-repair'
    assert plan['authorization'] is False
    assert plan['preconditions']['uid'] == 'uid-1'
    assert plan['preconditions']['resourceVersion'] == 'opaque-rv'
    assert plan == MODULE.decide(evidence)  # duplicate input is idempotent


@pytest.mark.parametrize('field,value,action', [
    ('observation', 'gap', 'observe'), ('observation', 'disconnected', 'observe'),
    ('observation', 'stale', 'observe'), ('acceptedValid', False, 'blocked'),
    ('hold', 'active', 'held'), ('hold', 'expired', 'held'), ('hold', 'unknown', 'held'),
    ('desiredInputDigest', 'unaccepted-change', 'await-acceptance'),
    ('ownership', 'runtime', 'observe'), ('ownership', 'unknown', 'review'),
    ('ownership', 'frozen', 'escalate'), ('changeClass', 'unattributed', 'review'),
    ('changeClass', 'conflict', 'review'), ('repairMode', 'review', 'review'),
    ('uid', '', 'unavailable'), ('acceptedValid', 'true', 'unavailable'),
    ('observation', [], 'unavailable'),
])
def test_safety_gates_do_not_generate_repair(evidence, field, value, action):
    evidence[field] = value
    result = MODULE.decide(evidence)
    assert result['action'] == action
    assert result['authorization'] is False
    assert 'planId' not in result


def test_status_only_never_generates_apply(evidence):
    evidence.update(observedSpecDigest='target', runtime='unhealthy')
    result = MODULE.decide(evidence)
    assert result['action'] == 'no-spec-change'
    assert result['runtime'] == 'unhealthy'


def test_hold_does_not_leak_across_service(evidence):
    held = dict(evidence, hold='active')
    other = dict(evidence, service='b', objectKey='apps/v1/Deployment/b/web')
    assert MODULE.decide(held)['action'] == 'held'
    assert MODULE.decide(other)['action'] == 'plan-repair'


@pytest.mark.parametrize('field', ['uid', 'resourceVersion', 'acceptedRevision',
    'policyRevision', 'observedSpecDigest', 'desiredRevision'])
def test_changed_precondition_invalidates_plan_identity(evidence, field):
    original = MODULE.decide(evidence)['planId']
    evidence[field] += '-changed'
    assert MODULE.decide(evidence)['planId'] != original


def test_unknown_or_missing_fields_fail_closed(evidence):
    assert MODULE.decide(dict(evidence, injectedOverride=True))['action'] == 'unavailable'
    del evidence['hold']
    assert MODULE.decide(evidence)['action'] == 'unavailable'
