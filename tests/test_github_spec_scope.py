"""Adversarial path admission tests; no cluster or GitHub writes."""
import copy
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'operations/migrations/github-spec-machine/scope_gate.py'
SPEC = importlib.util.spec_from_file_location('spec_scope_gate', SOURCE)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
POLICY = {'version': 1, 'protected': ['.github', 'platform/network'], 'services': {
    'a': {'actorIds': [42], 'paths': ['services/a']},
    'b': {'actorIds': [43], 'paths': ['services/b']},
}}


def check(files, **kwargs):
    args = dict(actor_id=42, service='a', files=files, changed_files=len(files))
    args.update(kwargs)
    return GATE.evaluate(POLICY, **args)


@pytest.mark.parametrize('status', ['added', 'modified', 'removed'])
def test_owned_changes_are_only_path_eligible(status):
    assert check([{'filename': 'services/a/config.json', 'status': status}]) == {
        'pathEligible': True, 'reasons': [], 'authorization': False}


@pytest.mark.parametrize('path', ['services/b/config', 'services/ab/config', '.github/x',
    'platform/network/x', '/services/a/x', 'services/a/../b/x', 'services/a//x',
    'services\\a\\x', 'services/a/x\n', 'services/a/./x'])
def test_path_bypasses_deny(path):
    assert not check([{'filename': path, 'status': 'modified'}])['pathEligible']


def test_rename_cannot_launder_another_services_file():
    result = check([{'filename': 'services/a/new', 'status': 'renamed',
                     'previous_filename': 'services/b/old'}])
    assert 'outside-service-scope' in result['reasons']


@pytest.mark.parametrize('override', [dict(actor_id=43), dict(actor_id='42'),
    dict(actor_id=True), dict(service='unknown'), dict(changed_files=2),
    dict(changed_files=3000)])
def test_identity_scope_and_completeness_fail_closed(override):
    assert not check([{'filename': 'services/a/x', 'status': 'added'}], **override)['pathEligible']


@pytest.mark.parametrize('files', [[], [None], [{'filename': 'services/a/x', 'status': 'copied'}],
    [{'filename': 'services/a/x', 'status': []}],
    [{'filename': 'services/a/x', 'status': 'renamed'}],
    [{'filename': 'services/a/x', 'status': 'added'}] * 2])
def test_malformed_diff_deny(files):
    assert not check(files)['pathEligible']


def test_protection_overrides_even_an_explicit_grant():
    policy = copy.deepcopy(POLICY)
    policy['services']['a']['paths'].append('platform/network')
    result = GATE.evaluate(policy, actor_id=42, service='a', changed_files=1,
        files=[{'filename': 'platform/network/x', 'status': 'modified'}])
    assert 'protected-path' in result['reasons']


def test_overlapping_services_deny():
    policy = copy.deepcopy(POLICY)
    policy['services']['b']['paths'] = ['services']
    result = GATE.evaluate(policy, actor_id=42, service='a', changed_files=1,
        files=[{'filename': 'services/a/x', 'status': 'added'}])
    assert 'ambiguous-ownership' in result['reasons']


@pytest.mark.parametrize('policy', [{}, {'version': 1, 'protected': [], 'services': {}},
    {'version': 1, 'protected': ['.github'], 'services': {'a': None}}])
def test_invalid_policy_deny(policy):
    assert GATE.evaluate(policy, actor_id=42, service='a', files=[], changed_files=0)['reasons'] == ['invalid-policy']


def test_shell_like_filename_is_only_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = check([{'filename': 'services/a/$(touch PWNED)', 'status': 'added'}])
    assert result['pathEligible']
    assert not (tmp_path / 'PWNED').exists()
