"""Fail-closed success evidence and actual execution/collection boundaries."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest
import yaml

from operations.ci import unit_evidence as e, unit_baseline as runner

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = {'repository': 'owner/repo', 'run_id': 123, 'attempt': 1, 'sha': 'a' * 40}


@pytest.fixture
def graph(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'data').mkdir()
    (tmp_path / 'operations/ci').mkdir(parents=True)
    (tmp_path / 'data/input.json').write_text('{"value": 1}')
    (tmp_path / 'tests/test_sample.py').write_text('''import json
from pathlib import Path
def test_value():
    assert json.loads(Path('data/input.json').read_text())['value'] == 1
''')
    (tmp_path / 'tests/test_always.py').write_text('def test_always(): assert True\n')
    # Explicitly use the real adapters while keeping fixture inputs isolated.
    (tmp_path / 'conftest.py').write_text('from operations.ci import unit_report\nunit_report.toolchain = lambda: {}\n')
    (tmp_path / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = [' + json.dumps(str(ROOT)) + ']\n')
    catalog = {'version': 1, 'domains': {}, 'focused_suites': {}, 'shared_paths': [], 'always_tests': ['tests/test_always.py'],
               'unit_defaults': {'helpers': ['conftest.py'], 'environment': ['pyproject.toml']},
               'validation_units': {'tests/test_sample.py': {'sources': [], 'data': ['data/**'], 'helpers': [], 'tools': [], 'reuse': 'candidate'}}}
    (tmp_path / 'operations/ci/domain-catalog.yaml').write_text(yaml.safe_dump(catalog))
    env = {'tools': {}, 'python': 'test'}
    return tmp_path, catalog, env


def fingerprint(graph):
    root, catalog, env = graph
    return e.Inputs(root).fingerprint('tests/test_sample.py', catalog, env)[0]


@pytest.mark.parametrize('mutation', ['content', 'add', 'delete', 'rename', 'test', 'helper', 'toolchain', 'declaration', 'gitlink'])
def test_inputs_invalidate(graph, mutation):
    root, catalog, env = graph
    before = fingerprint(graph)
    path = root / 'data/input.json'
    if mutation == 'content': path.write_text('{"value": 2}')
    elif mutation == 'add': (root / 'data/new.yaml').write_text('a: 1')
    elif mutation == 'delete': path.unlink()
    elif mutation == 'rename': path.rename(root / 'data/moved.json')
    elif mutation == 'test': (root / 'tests/test_sample.py').write_text('def test_changed(): pass')
    elif mutation == 'helper':
        (root / 'helper.py').write_text('a=1')
        catalog['unit_defaults']['helpers'] = ['helper.py']
    elif mutation == 'toolchain': env['python'] = 'changed'
    elif mutation == 'declaration': catalog['version'] += 1
    elif mutation == 'gitlink':
        subprocess.run(['git', 'update-index', '--add', '--cacheinfo', '160000,' + 'b'*40 + ',data/submodule'], cwd=root, check=True)
    assert fingerprint(graph) != before


def test_unknown_inputs_force_real_execution_and_shared_declarations_cover_adapter(graph):
    from operations.ci import impact, unit_catalog
    assert impact.select(['operations/unknown.py'])['full']
    catalog = unit_catalog.load_catalog()
    for path in ('operations/ci/unit_baseline.py', 'operations/ci/unit_evidence.py', 'operations/ci/unit_collection.py'):
        assert path in unit_catalog.inputs_for('tests/test_git_k8s_manifests.py', catalog)


def test_symlinks_and_unclosed_inputs_cannot_cache(graph):
    root, catalog, env = graph
    (root / 'data/link').symlink_to(root / 'data/input.json')
    assert e.Inputs(root).fingerprint('tests/test_sample.py', catalog, env) == (None, 'symlink-input')
    catalog['validation_units']['tests/test_sample.py']['reuse_blocked'] = 'Git state'
    assert e.Inputs(root).fingerprint('tests/test_sample.py', catalog, env) == (None, 'Git state')


def fixture_api():
    created = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    run = {'id': 123, 'workflow_id': 7, 'path': '.github/workflows/main-unit-baseline.yml', 'event': 'push',
           'head_branch': 'main', 'head_sha': 'a'*40, 'repository': {'full_name': 'owner/repo'},
           'head_repository': {'full_name': 'owner/repo'}, 'status': 'completed', 'conclusion': 'success',
           'created_at': created, 'run_attempt': 1}
    payload = {'version': 1, 'publisher': PUBLISHER, 'units': {}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive: archive.writestr('evidence.json', json.dumps(payload))
    raw = buffer.getvalue()
    artifact = {'id': 456, 'name': e.ARTIFACT, 'expired': False, 'size_in_bytes': len(raw),
                'workflow_run': {'id': 123, 'head_sha': 'a'*40}, 'digest': 'sha256:'+hashlib.sha256(raw).hexdigest()}
    calls = []
    def request(path, binary=False):
        calls.append(path)
        if path.endswith('/zip'): return raw
        if '/artifacts?' in path: return {'artifacts': [artifact]}
        if '/runs?' in path: return {'workflow_runs': [run] if 'event=push' in path else []}
        return {'id': 7}
    return request, run, artifact, calls


@pytest.mark.parametrize('failure', [None, 'branch', 'repository', 'event', 'workflow', 'failed', 'expired', 'digest', 'oversized', 'missing', 'download'])
def test_only_latest_trusted_success_is_consumed(failure):
    request, run, artifact, calls = fixture_api()
    if failure == 'branch': run['head_branch'] = 'feature'
    elif failure == 'repository': run['head_repository']['full_name'] = 'fork/repo'
    elif failure == 'event': run['event'] = 'pull_request'
    elif failure == 'workflow': run['path'] = '.github/workflows/other.yml'
    elif failure == 'failed': run['conclusion'] = 'failure'
    elif failure == 'expired': artifact['expired'] = True
    elif failure == 'digest': artifact['digest'] = 'sha256:bad'
    elif failure == 'oversized': artifact['size_in_bytes'] = e.MAX_BYTES + 1
    elif failure == 'missing': artifact['name'] = 'other'
    if failure == 'download':
        original = request
        def request(path, binary=False):
            if binary: raise OSError('download failed')
            return original(path, binary)
    baseline, reason = e.latest_baseline('owner/repo', request=request)
    assert (baseline is not None) == (failure is None)
    assert sum(path.endswith('/zip') for path in calls) <= 1
    assert all('page=2' not in path for path in calls)


def test_expired_record_case_change_and_inheritance_do_not_refresh_age():
    record = {'fingerprint': 'key', 'cases': ['case'], 'result': 'passed', 'seconds': 1,
              'verified_at': time.time(), 'origin': PUBLISHER}
    assert e.match(record, 'key', ['case'])[0]
    assert not e.match(record, 'key', ['case'], repository='other/repo')[0]
    assert not e.match(record, 'other', ['case'])[0]
    assert not e.match(record, 'key', ['new-case'])[0]
    record['verified_at'] -= e.MAX_AGE + 1
    assert not e.match(record, 'key', ['case'])[0]


def test_real_cold_hit_partial_failure_and_forced_regression(graph, tmp_path, monkeypatch):
    root, catalog, env = graph
    # Child pytest imports the adapters through fixture pyproject.pythonpath.
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    monkeypatch.setattr(runner.unit_report, 'toolchain', lambda: env)
    plan = {'tests': [], 'paths': ['README.md'], 'full': False, 'jobs': {'unit': False},
            'domains': [], 'focused_suites': [], 'risky_unknown': []}
    def execute(name, baseline=None, full=False):
        dest = tmp_path.parent / (tmp_path.name + '-' + name)
        code = runner.run(root, plan, dest, PUBLISHER, baseline=baseline, full=full)
        report = json.loads((dest / 'units.json').read_text())
        success = json.loads((dest / 'evidence.json').read_text()) if (dest / 'evidence.json').exists() else None
        return code, report, success
    code, cold, baseline = execute('cold')
    assert code == 0 and baseline['units']['tests/test_sample.py']
    code, hit, inherited = execute('hit', baseline)
    assert code == 0 and hit['units']['tests/test_sample.py']['status'] == 'reused'
    assert 'cases' not in hit['units']['tests/test_sample.py']  # No fake execution/JUnit phases.
    assert inherited['units']['tests/test_sample.py']['verified_at'] == baseline['units']['tests/test_sample.py']['verified_at']
    summary = (root / 'summary.md').read_text()
    assert 'original run' in summary and '1 cases' in summary and 'reused_success' in summary
    code, forced, _ = execute('forced', baseline, full=True)
    assert code == 0 and forced['units']['tests/test_sample.py']['status'] == 'executed'
    (root / 'data/input.json').write_text('{"value": 2}')
    code, broken, success = execute('broken', baseline)
    assert code == 1 and broken['units']['tests/test_sample.py']['status'] == 'failed' and success is None


def test_workflows_separate_main_evidence_from_pr_and_force_daily_runs():
    workflow = (ROOT / '.github/workflows/main-unit-baseline.yml').read_text()
    daily = (ROOT / '.github/workflows/full-regression.yml').read_text()
    assert 'branches: [main]' in workflow and 'pull_request' not in workflow
    assert 'actions: read' in workflow and 'actions: write' not in workflow
    assert 'unit_baseline --full' in daily
    assert 'name: unit-success-evidence' in workflow and 'retention-days: 14' in workflow
    assert 'unit_baseline' not in (ROOT / '.github/workflows/ci.yml').read_text()


@pytest.mark.parametrize('body', ["import pytest\n@pytest.mark.skip\ndef test_value(): pass\n", "import pytest\n@pytest.mark.xfail\ndef test_value(): assert False\n", "import pytest\n@pytest.mark.xfail\ndef test_value(): pass\n"])
def test_skips_xfail_and_xpass_never_certify_a_unit(graph, tmp_path, monkeypatch, body):
    root, catalog, environment = graph
    (root / 'tests/test_sample.py').write_text(body)
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    monkeypatch.setattr(runner.unit_report, 'toolchain', lambda: environment)
    plan = {'tests': [], 'paths': [], 'full': False, 'jobs': {'unit': True}, 'domains': [], 'focused_suites': [], 'risky_unknown': []}
    out = tmp_path.parent / (tmp_path.name + '-out')
    assert runner.run(root, plan, out, PUBLISHER) == 0
    assert json.loads((out / 'evidence.json').read_text())['units'] == {}


def test_pr_cannot_publish_even_with_a_baseline_argument(monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/1/merge')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('GITHUB_WORKFLOW_REF', 'owner/repo/.github/workflows/main-unit-baseline.yml@refs/heads/main')
    monkeypatch.setattr(sys, 'argv', ['unit_baseline', '--output', str(tmp_path)])
    with pytest.raises(SystemExit) as error: runner.main()
    assert error.value.code == 2
    assert not (tmp_path / 'evidence.json').exists()


@pytest.mark.parametrize('damage', ['version', 'publisher', 'unit-type', 'json', 'zip-path'])
def test_corrupt_or_incompatible_latest_artifact_falls_back(damage):
    request, run, artifact, calls = fixture_api()
    payload = {'version': e.VERSION, 'publisher': dict(PUBLISHER), 'units': {}}
    if damage == 'version': payload['version'] += 1
    elif damage == 'publisher': payload['publisher']['sha'] = 'b'*40
    elif damage == 'unit-type': payload['units'] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('../evidence.json' if damage == 'zip-path' else 'evidence.json',
                         '{invalid' if damage == 'json' else json.dumps(payload))
    raw = buffer.getvalue()
    artifact['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    artifact['size_in_bytes'] = len(raw)
    def changed(path, binary=False):
        return raw if binary else request(path)
    baseline, reason = e.latest_baseline('owner/repo', request=changed)
    assert baseline is None
