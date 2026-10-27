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
        run_dir = tmp_path / job
        run_dir.mkdir()
        (run_dir / 'state-patch.json').write_text('{}')
        (run_dir / 'result.json').write_text('{"outcome":"healthy","health":{"status":"healthy"}}')
        bins = run_dir / 'bin'
        bins.mkdir()
        curl = bins / 'curl'
        curl.write_text('#!/bin/sh\n[ "$REFRESH_FAIL" = no ] || exit 22\nprintf \'{"value":"fresh-fixture-token"}\'\n')
        curl.chmod(0o755)
        kubectl = bins / 'kubectl'
        kubectl.write_text('''#!/bin/sh
case "$1 $2" in
  'config set-credentials')
    [ "$3" = release-executor ] && [ "$4" = --token=fresh-fixture-token ] || exit 31
    touch "$RUNNER_TEMP/refreshed" ;;
  'patch configmap')
    [ -f "$RUNNER_TEMP/refreshed" ] || exit 32
    touch "$RUNNER_TEMP/patched" ;;
  *) exit 33 ;;
esac
''')
        kubectl.chmod(0o755)
        env = {**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'], 'RUNNER_TEMP': str(run_dir),
               'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'fixture', 'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://fixture.invalid',
               'EXECUTOR_AUDIENCE': 'https://kube-api.re8ch.com/release-executor-test', 'REFRESH_FAIL': 'no',
               'state_name': 'ssa-state-demo', 'TARGET_NAMESPACE': 'ops', 'executor_status': '0'}
        subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, check=True, capture_output=True)
        assert (run_dir / 'patched').exists()
        (run_dir / 'patched').unlink()
        (run_dir / 'refreshed').unlink()
        env['REFRESH_FAIL'] = 'yes'
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, capture_output=True)
        assert result.returncode != 0
        assert not (run_dir / 'patched').exists()
