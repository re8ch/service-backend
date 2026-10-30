import importlib.util
import json
from pathlib import Path
import sys
import pytest
import yaml

DIR = Path(__file__).resolve().parents[1] / 'operations/infrastructure-contract/recovery'
sys.path.insert(0, str(DIR))
import restore
import render


def fake_plain(name):
    return {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': 'ai-operations', 'annotations': {'operations.re8ch.com/source-digest': restore.SOURCE}}, 'stringData': {k: 'CONFIDENTIAL-FIXTURE' for k in restore.TARGETS[name]}}


def test_decode_enforces_fixed_identity_keys_source_and_mac_success(monkeypatch):
    name = next(iter(restore.TARGETS))
    cipher = json.dumps({'sops': {'age': [{'recipient': restore.RECIPIENT}]}}).encode()
    plain = fake_plain(name)
    monkeypatch.setattr(restore, 'sops', lambda *a: json.dumps(plain).encode())
    assert set(restore.decode(name, cipher)) == restore.TARGETS[name]
    for field, value in [('namespace', 'other'), ('name', 'other')]:
        old = plain['metadata'][field]; plain['metadata'][field] = value
        with pytest.raises(restore.Rejected): restore.decode(name, cipher)
        plain['metadata'][field] = old
    plain['stringData']['extra'] = 'bad'
    with pytest.raises(restore.Rejected): restore.decode(name, cipher)


def test_sops_mac_failure_suppresses_output():
    class Result: returncode = 1; stdout = b'CONFIDENTIAL-FIXTURE'
    def runner(*a, **kw):
        assert kw['stderr'] == restore.subprocess.DEVNULL
        assert kw['env']['SOPS_AGE_KEY_FILE'] == restore.KEY
        return Result()
    with pytest.raises(restore.Rejected): restore.sops([], b'x', runner)


def test_patch_is_cas_and_preserves_unrelated_metadata():
    p = restore.patch('infrastructure-contracts', {'compat-nodes.yaml': 'Zg=='}, {'uid': 'u', 'resourceVersion': '12'})
    assert [x['op'] for x in p[:3]] == ['test'] * 3
    assert [x['path'] for x in p[:3]] == ['/metadata/uid', '/metadata/resourceVersion', '/metadata/labels/operations.re8ch.com~1recovery']
    assert not any(x['path'] == '/metadata' for x in p)
    with pytest.raises(restore.Rejected): restore.patch('infrastructure-contracts', {}, {'uid': 'REPLACE', 'resourceVersion': '12'})


def test_render_has_patch_only_identity_and_api_only_network():
    docs = render.render()
    assert next(d for d in docs if d['kind'] == 'Job')['metadata']['name'] == 'infra-contracts-recovery-dryrun-v2'
    role = next(d for d in docs if d['kind'] == 'Role')
    assert role['rules'] == [{'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': list(restore.TARGETS), 'verbs': ['patch']}]
    policy = next(d for d in docs if d['kind'] == 'CiliumNetworkPolicy')
    assert policy['spec']['egress'][0]['toEntities'] == ['kube-apiserver']
    pod = next(d for d in docs if d['kind'] == 'Job')['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert all(v['emptyDir']['medium'] == 'Memory' for v in pod['volumes'] if 'emptyDir' in v)
    assert 'identity' not in {v['name'] for v in pod['initContainers'][0]['volumeMounts']}
    assert 'age' not in {v['name'] for v in pod['containers'][0]['volumeMounts']}
    assert '@sha256:' in pod['containers'][0]['image']
    assert '--apply' not in pod['containers'][0]['command']


def make_input(tmp_path):
    data = next(d for d in render.render() if d['kind'] == 'ConfigMap')['data']
    for name, content in data.items(): (tmp_path / name).write_text(content)
    (tmp_path / 'targets.json').write_text(json.dumps({n: {'uid': n, 'resourceVersion': '12'} for n in restore.TARGETS}))
    (tmp_path / 'prepared.json').write_text(json.dumps({'sourceDigest': restore.SOURCE, 'data': {n: {k: 'Zml4dHVyZQ==' for k in restore.TARGETS[n]} for n in restore.TARGETS}}))


def test_all_preflights_precede_apply_and_sanitized_result(tmp_path, monkeypatch):
    make_input(tmp_path)
    monkeypatch.setattr(restore, 'decode', lambda name, c: {k: 'CONFIDENTIAL' for k in restore.TARGETS[name]})
    monkeypatch.setattr(restore, 'verify_key', lambda: None)
    calls = []
    def send(name, ops, dry):
        calls.append(dry)
        return {'metadata': {'uid': name}, 'data': ops[3]['value']}
    monkeypatch.setattr(restore, 'send', send)
    result = restore.run(tmp_path, False, tmp_path)
    assert calls == [True, True, False, False]
    assert 'CONFIDENTIAL' not in json.dumps(result)


def test_failed_second_preflight_never_writes(tmp_path, monkeypatch):
    make_input(tmp_path)
    monkeypatch.setattr(restore, 'decode', lambda name, c: {k: 'x' for k in restore.TARGETS[name]})
    monkeypatch.setattr(restore, 'verify_key', lambda: None)
    calls = []
    def send(name, ops, dry):
        calls.append(dry)
        if len(calls) == 2: raise restore.Rejected()
        return {'metadata': {'uid': name}, 'data': ops[3]['value']}
    monkeypatch.setattr(restore, 'send', send)
    with pytest.raises(restore.Rejected): restore.run(tmp_path, False, tmp_path)
    assert calls == [True, True]


def test_cipher_change_fails_before_sops(tmp_path, monkeypatch):
    make_input(tmp_path)
    first = next(iter(restore.TARGETS))
    (tmp_path / (first + '.json')).write_text('{}')
    monkeypatch.setattr(restore, 'decode', lambda *a: pytest.fail('untrusted cipher reached SOPS'))
    with pytest.raises(restore.Rejected): restore.prepare(tmp_path, tmp_path)


@pytest.mark.parametrize('stage, failure', [
    ('key_challenge', 'verify_key'),
    ('source_decrypt_and_validate', 'decode'),
])
def test_prepare_reports_only_fixed_stage(tmp_path, monkeypatch, stage, failure):
    make_input(tmp_path)
    monkeypatch.setattr(restore, 'verify_key', lambda: None)
    monkeypatch.setattr(restore, 'decode', lambda name, c: {k: 'CONFIDENTIAL-FIXTURE' for k in restore.TARGETS[name]})
    def fail(*args): raise ValueError('CONFIDENTIAL-FIXTURE')
    monkeypatch.setattr(restore, failure, fail)
    with pytest.raises(restore.DiagnosticRejected) as error:
        restore.prepare(tmp_path, tmp_path)
    assert error.value.stage == stage


def test_fixed_stage_log_never_emits_private_error(capsys, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['restore.py', '--prepare'])
    def fail(*args): raise restore.DiagnosticRejected('key_challenge')
    monkeypatch.setattr(restore, 'prepare', fail)
    assert restore.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)['stage'] == 'key_challenge'
    assert 'CONFIDENTIAL-FIXTURE' not in output


def test_errors_and_redirects_never_emit_payload(capsys, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['restore.py'])
    def fail(*a, **kw): raise ValueError('CONFIDENTIAL')
    monkeypatch.setattr(restore, 'run', fail)
    assert restore.main() == 1
    assert 'CONFIDENTIAL' not in capsys.readouterr().out
    with pytest.raises(restore.Rejected): restore.NoRedirect().redirect_request()


def test_checked_in_candidate_matches_renderer():
    assert list(yaml.safe_load_all((DIR / 'candidate.yaml').read_text())) == render.render()


def test_real_sops_mac_survives_yaml_to_json_projection(tmp_path, monkeypatch):
    import shutil
    import subprocess
    if not shutil.which('sops') or not shutil.which('age-keygen'):
        pytest.skip('optional local fixture tools not installed')
    key = tmp_path / 'fixture-age-key'
    subprocess.run(['age-keygen', '-o', str(key)], check=True, capture_output=True)
    recipient = subprocess.check_output(['age-keygen', '-y', str(key)], text=True).strip()
    monkeypatch.setattr(restore, 'KEY', str(key))
    monkeypatch.setattr(restore, 'RECIPIENT', recipient)
    name = 'infrastructure-contracts'
    plain = yaml.safe_dump(fake_plain(name)).encode()
    encrypted = subprocess.run(['sops', 'encrypt', '--age', recipient, '--encrypted-regex', '^(data|stringData)$', '--input-type', 'yaml', '--output-type', 'yaml', '/dev/stdin'], input=plain, check=True, capture_output=True, cwd=tmp_path).stdout
    projected = json.dumps(yaml.safe_load(encrypted), sort_keys=True, separators=(',', ':')).encode()
    assert set(restore.decode(name, projected)) == restore.TARGETS[name]
    restore.verify_key()


def test_preflight_has_no_identity_or_key_or_network():
    docs = list(yaml.safe_load_all((DIR / 'preflight.yaml').read_text()))
    policy = docs[0]['spec']
    assert policy['egress'] == [] and policy['ingress'] == []
    assert policy['enableDefaultDeny'] == {'ingress': True, 'egress': True}
    pod = docs[1]['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert not pod.get('volumes') and not pod['containers'][0].get('volumeMounts')
    assert pod['containers'][0]['image'] == render.IMAGE
