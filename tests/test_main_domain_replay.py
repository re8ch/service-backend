from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'operations/releases'))
import main_domain_profile as profile


def test_replay_matrix_is_exact_and_excludes_external_authorities():
    entries = profile.replay_matrix()
    assert entries
    assert {entry['domain'] for entry in entries} == {'data-storage', 'commerce-billing', 'operations-ui', 'tenant-runtime'}
    for entry in entries:
        contract = yaml.safe_load((ROOT / entry['contract']).read_text())
        assert entry['environment'] == 'release-reconciler-' + entry['domain']
        assert entry['audience'] == 'https://kube-api.re8ch.com/' + entry['environment']
        assert contract['spec']['provenance']['environment'] != entry['environment']
        assert entry['signer'] == contract['spec']['provenance']['signer']
        assert profile.validate_contract(Path(entry['contract']))['namespace'] == entry['namespace']


def test_reconcile_workflow_has_no_publish_or_caller_artifact_input():
    raw = (ROOT / '.github/workflows/main-domain-reconcile.yml').read_text()
    workflow = yaml.safe_load(raw)
    triggers = workflow.get('on', workflow.get(True))
    assert triggers['workflow_dispatch'] is None
    assert triggers['schedule']
    assert triggers['push']['branches'] == ['main']
    job = workflow['jobs']['reconcile']
    assert job['permissions'] == {'contents': 'read', 'id-token': 'write', 'packages': 'read'}
    assert job['environment'] == '${{ matrix.environment }}'
    assert job['concurrency'] == {'group': 'main-domain-release-${{ matrix.contract }}', 'cancel-in-progress': False}
    assert 'flux push' not in raw and 'cosign sign' not in raw and 'kubectl kustomize' not in raw
    assert 'accepted-bundle --configmap' in raw and 'request --replay-only' in raw
    assert 'No accepted artifact' in raw
    assert 'state-patch' in raw and 'cosign verify' in raw
    assert '--force-conflicts' not in raw


def test_durable_state_write_refreshes_expired_outer_identity(tmp_path):
    import os
    import subprocess
    for workflow, job in [('main-domain-release.yml', 'execute'), ('main-domain-reconcile.yml', 'reconcile')]:
        definition = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())
        script = next(step['run'] for step in definition['jobs'][job]['steps'] if 'state-patch --configmap' in step.get('run', ''))
        script = script[script.index('if [[ -s "$RUNNER_TEMP/state-patch.json"'):]
        assert '--token=' not in script and 'Authorization: bearer' not in script
        run_dir = tmp_path / job
        run_dir.mkdir()
        (run_dir / 'state-patch.json').write_text('{}')
        (run_dir / 'result.json').write_text('{"outcome":"healthy","health":{"status":"healthy"}}')
        bins = run_dir / 'bin'
        bins.mkdir()
        for name, body in {
            'python': '[ "$1" = operations/releases/refresh_kubeconfig.py ] && [ "$REFRESH_FAIL" = no ] || exit 22\ntouch "$RUNNER_TEMP/refreshed"',
            'kubectl': '[ "$1 $2" = "patch configmap" ] && [ -f "$RUNNER_TEMP/refreshed" ] || exit 32\ntouch "$RUNNER_TEMP/patched"',
        }.items():
            path = bins / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)
        env = {**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'], 'RUNNER_TEMP': str(run_dir),
               'REFRESH_FAIL': 'no', 'state_name': 'ssa-state-demo', 'TARGET_NAMESPACE': 'ops', 'executor_status': '0'}
        subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, check=True, capture_output=True)
        assert (run_dir / 'patched').exists()
        (run_dir / 'patched').unlink()
        (run_dir / 'refreshed').unlink()
        env['REFRESH_FAIL'] = 'yes'
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, capture_output=True)
        assert result.returncode != 0
        assert not (run_dir / 'patched').exists()


def test_refresh_uses_existing_oidc_guards_and_atomic_private_kubeconfig(tmp_path, monkeypatch, capsys):
    import refresh_kubeconfig as refresh
    import pytest
    path = tmp_path / 'kubeconfig'
    original = {'users': [{'name': 'release-executor', 'user': {'token': 'old'}},
                          {'name': 'other', 'user': {'token': 'unchanged'}}],
                'current-context': 'release-executor', 'clusters': [{'name': 'cluster'}]}
    path.write_text(yaml.safe_dump(original))
    monkeypatch.setenv('KUBECONFIG', str(path))
    monkeypatch.setattr(refresh.Executor, 'actions_kube_token', lambda self: 'fresh-fixture-token')
    refresh.refresh()
    updated = yaml.safe_load(path.read_text())
    assert updated['users'][0]['user'] == {'token': 'fresh-fixture-token'}
    assert updated['users'][1:] == original['users'][1:]
    assert updated['clusters'] == original['clusters']
    assert path.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr() == ('', '')
    before = path.read_bytes()
    monkeypatch.setattr(refresh.Executor, 'actions_kube_token', lambda self: None)
    with pytest.raises(refresh.Rejected):
        refresh.refresh()
    assert path.read_bytes() == before
