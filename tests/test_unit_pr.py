"""PR consumes only selected units and can never publish a main baseline."""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest
import yaml

from operations.ci import unit_baseline as runner, unit_pr, impact
from test_unit_evidence import graph, PUBLISHER, ROOT


def plan(*tests, full=False):
    return {'tests': list(tests), 'paths': ['README.md'], 'full': full,
            'jobs': {'unit': bool(tests)}, 'domains': [], 'focused_suites': [], 'risky_unknown': []}


@pytest.fixture
def consumer(graph, tmp_path, monkeypatch):
    root, catalog, environment = graph
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    monkeypatch.setattr(runner.unit_report, 'toolchain', lambda: environment)
    out = tmp_path.parent / (tmp_path.name + '-main')
    assert runner.run(root, plan(), out, PUBLISHER) == 0
    baseline = json.loads((out / 'evidence.json').read_text())
    def execute(name, selected, lookup=None):
        output = tmp_path.parent / (tmp_path.name + '-' + name)
        # Stale evidence must also be removed in consume-only mode.
        output.mkdir()
        (output / 'evidence.json').write_text('stale')
        code = unit_pr.consume(root, selected, output, PUBLISHER,
                               lookup=lookup or (lambda repository: (baseline, 'latest-trusted-baseline')))
        assert not (output / 'evidence.json').exists()
        return code, json.loads((output / 'units.json').read_text()), output
    return root, baseline, execute


def test_hit_does_not_execute_cached_test_or_fixture_and_does_not_publish(consumer):
    root, baseline, execute = consumer
    code, result, output = execute('hit', plan('tests/test_sample.py', 'tests/test_always.py'))
    assert code == 0
    assert result['units']['tests/test_sample.py']['status'] == 'reused'
    assert result['units']['tests/test_always.py']['status'] == 'executed'
    junit = list(ET.parse(output / 'execution/junit.xml').iter('testcase'))
    assert [case.get('name') for case in junit] == ['test_always']
    assert 'cases' not in result['units']['tests/test_sample.py']
    assert result['units']['tests/test_sample.py']['evidence']['case_count'] == 1
    assert {'lookup', 'collection', 'fingerprint', 'execution', 'post_validation'} <= result['phases_seconds'].keys()


def test_selected_only_and_docs_do_not_start_extra_work(consumer, monkeypatch):
    root, baseline, execute = consumer
    def forbidden(*args, **kwargs): raise AssertionError('unexpected subprocess/API request')
    code, result, _ = execute('always-only', plan('tests/test_always.py'), lookup=forbidden)
    assert code == 0
    assert result['units']['tests/test_sample.py']['status'] == 'unaffected'
    monkeypatch.setattr(runner.subprocess, 'run', forbidden)
    docs = plan('tests/test_always.py')
    docs['jobs']['unit'] = False
    code, result, _ = execute('docs', docs, lookup=forbidden)
    assert code == 0 and set(result['counts']) == {'unaffected'}


@pytest.mark.parametrize('reason', ['no-baseline', 'baseline-unavailable-or-invalid', 'expired-baseline'])
def test_missing_permission_download_or_expired_baseline_runs_selected(consumer, reason):
    root, baseline, execute = consumer
    code, result, _ = execute(reason, plan('tests/test_sample.py', 'tests/test_always.py'),
                              lookup=lambda repo: (None, reason))
    assert code == 0 and result['units']['tests/test_sample.py']['status'] == 'executed'
    assert {'code': reason} in result['units']['tests/test_sample.py']['reasons']


def test_forced_full_does_not_download_or_reuse(consumer):
    root, baseline, execute = consumer
    def forbidden(*args): raise AssertionError('forced run must not download')
    code, result, _ = execute('full', plan('tests/test_sample.py', 'tests/test_always.py', full=True), lookup=forbidden)
    assert code == 0 and result['units']['tests/test_sample.py']['status'] == 'executed'
    assert not result['reuse_enabled']


def test_input_mutation_runs_original_assertion_and_fails(consumer):
    root, baseline, execute = consumer
    (root / 'data/input.json').write_text('{"value":2}')
    code, result, _ = execute('changed', plan('tests/test_sample.py', 'tests/test_always.py'))
    assert code == 1 and result['units']['tests/test_sample.py']['status'] == 'failed'
    assert {'code': 'fingerprint-mismatch'} in result['units']['tests/test_sample.py']['reasons']


def test_changed_case_evidence_cannot_skip_actual_test(consumer):
    root, baseline, execute = consumer
    baseline['units']['tests/test_sample.py']['cases'] = ['tests/test_sample.py::missing']
    code, result, _ = execute('cases', plan('tests/test_sample.py', 'tests/test_always.py'))
    assert code == 0 and result['units']['tests/test_sample.py']['status'] == 'executed'
    assert {'code': 'case-set-mismatch'} in result['units']['tests/test_sample.py']['reasons']


def test_execution_missing_report_blocks_current_check(consumer, monkeypatch):
    root, baseline, execute = consumer
    actual_run = runner.subprocess.run
    def missing_report(args, **kwargs):
        if not any(str(arg).startswith('--unit-report-dir=') for arg in args):
            return actual_run(args, **kwargs)
        return subprocess.CompletedProcess(args, 0)  # Simulate silent interruption/observer loss.
    monkeypatch.setattr(runner.subprocess, 'run', missing_report)
    code, result, _ = execute('missing', plan('tests/test_sample.py', 'tests/test_always.py'))
    assert code == 1 and result['units']['tests/test_always.py']['status'] == 'blocked'


def test_collection_failure_is_blocked_not_reused(consumer):
    root, baseline, execute = consumer
    (root / 'tests/test_sample.py').write_text('not valid python !!!')
    code, result, _ = execute('collect-failure', plan('tests/test_sample.py', 'tests/test_always.py'))
    assert code != 0 and result['units']['tests/test_sample.py']['status'] == 'blocked'


def test_selector_cannot_be_omitted(consumer):
    root, baseline, execute = consumer
    with pytest.raises(ValueError, match='always-real'):
        execute('omitted', plan('tests/test_sample.py'))


def test_workflow_uses_read_only_consumer_and_retains_required_entry():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    unit = workflow['jobs']['unit']
    assert unit['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert unit['if'] == "fromJSON(needs.impact.outputs.jobs)['unit']"
    assert 'unit' in workflow['jobs']['control-plane-validate']['needs']
    steps = unit['steps']
    assert any('operations.ci.unit_pr' in step.get('run', '') for step in steps)
    assert not any(step.get('with', {}).get('name') == 'unit-success-evidence' for step in steps)
    assert not impact.select(['README.md'])['jobs']['unit']


def test_large_plan_reaches_child_pytest_without_environment_limit(consumer):
    _, _, execute = consumer
    selected = plan('tests/test_always.py', full=True)
    selected['paths'] = ['historical/' + 'x' * 200 + str(i) for i in range(2000)]
    assert len(json.dumps(selected)) > 128 * 1024
    code, result, output = execute('large-plan', selected)
    assert code == 0
    assert result['units']['tests/test_always.py']['status'] == 'executed'
    assert json.loads((output / 'execution-plan.json').read_text())['paths'] == selected['paths']
