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
