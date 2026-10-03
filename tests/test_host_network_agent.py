import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('host_agent', Path(__file__).parents[1] / 'clusters/re8ch/components/network/host-network-controller/agent.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)

KEY = 'A' * 43 + '='


def package():
    return {'node': 'pilot', 'scope': 'public-mesh-peers', 'revision': 'a' * 64, 'timeout': 30,
            'wireguard': {'interface': 'wg-public-mesh', 'mtu': 1280, 'peers': [
                {'publicKey': KEY, 'allowedIPs': ['192.0.2.125/32', '192.0.2.179/32'], 'endpoint': '192.0.2.216:51820', 'keepalive': 15}]},
            'prefixLists': {'FROM-128': ['ip prefix-list FROM-128 seq 10 permit 192.0.2.179/32']},
            'health': {'bgpPeers': ['192.0.2.125'], 'checks': ['/usr/local/lib/re8ch-host-network/checks/fabric']}}


def adoption():
    return {'node': 'pilot', 'scope': 'public-mesh-peers', 'peerKeys': [KEY], 'prefixLists': ['FROM-128']}


def test_prefix_authority_and_scope():
    p = package()
    a.validate(p, adoption())
    p['wireguard']['peers'][0]['allowedIPs'].append('0.0.0.0/0')
    with pytest.raises(ValueError, match='outside'):
        a.validate(p, adoption())
    p = package()
    p['prefixLists']['SELF'] = []
    with pytest.raises(ValueError, match='ownership'):
        a.validate(p, adoption())


def test_persistent_merge_preserves_private_key_and_unowned_peer():
    raw = '[Interface]\nPrivateKey = HOST-ONLY\nPostUp = keep-this\nMTU = 1420\n\n[Peer]\nPublicKey = ' + KEY + '\nAllowedIPs = 192.0.2.125/32\n\n[Peer]\nPublicKey = OTHER\nAllowedIPs = 192.0.2.18/16\n'
    patched = a.patch_wg_config(raw, package()['wireguard'])
    assert 'PrivateKey = HOST-ONLY' in patched
    assert 'PostUp = keep-this' in patched
    assert '[Peer]\nPublicKey = OTHER\nAllowedIPs = 192.0.2.18/16' in patched
    assert 'MTU = 1280' in patched and 'MTU = 1420' not in patched
    assert patched == a.patch_wg_config(patched, package()['wireguard'])


def test_apply_failure_rolls_back_and_blocks_revision(tmp_path, monkeypatch):
    p = package()
    baseline = {'wireguard': {}, 'prefixLists': {}}
    monkeypatch.setattr(a, 'snapshot', lambda p: baseline)
    monkeypatch.setattr(a, 'healthy', lambda p: True)
    monkeypatch.setattr(a, 'syntax_check', lambda p: None)
    calls = []
    def apply(value, restore=False):
        calls.append(restore)
        if not restore:
            assert json.loads((tmp_path / 'transaction.json').read_text())['state'] == 'applying'
            raise RuntimeError('injected interruption')
    monkeypatch.setattr(a, 'apply_scopes', apply)
    with pytest.raises(RuntimeError):
        a.reconcile(tmp_path, p, adoption())
    assert calls == [False, True]
    assert a.reconcile(tmp_path, p, adoption()) == 'blocked'


def test_offline_watchdog_recovers_expired_transaction(tmp_path, monkeypatch):
    a.save(tmp_path / 'transaction.json', {'state': 'verifying', 'deadline': 0, 'digest': 'x', 'baseline': {'local': True}})
    calls = []
    monkeypatch.setattr(a, 'apply_scopes', lambda p, restore=False: calls.append((p, restore)))
    a.recover(tmp_path)
    assert calls == [({'local': True}, True)]
    assert json.loads((tmp_path / 'transaction.json').read_text())['state'] == 'rolled-back'


def test_failed_rollback_remains_retryable(tmp_path, monkeypatch):
    a.save(tmp_path / 'transaction.json', {'state': 'applying', 'deadline': 0, 'digest': 'x', 'baseline': {}})
    monkeypatch.setattr(a, 'apply_scopes', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('frr unavailable')))
    with pytest.raises(RuntimeError):
        a.recover(tmp_path)
    assert json.loads((tmp_path / 'transaction.json').read_text())['state'] == 'rolling-back'
    assert json.loads((tmp_path / 'blocked.json').read_text())['reason'] == 'rollback-failed'


def test_unhealthy_baseline_never_mutates(tmp_path, monkeypatch):
    monkeypatch.setattr(a, 'snapshot', lambda p: {'wireguard': {}, 'prefixLists': {}})
    monkeypatch.setattr(a, 'healthy', lambda p: False)
    assert a.reconcile(tmp_path, package(), adoption()) == 'preflight-unhealthy'
    assert not (tmp_path / 'transaction.json').exists()


def test_rollback_restores_persistent_fields_separately():
    raw = '[Interface]\nPrivateKey = PRIVATE\n\n[Peer]\nPublicKey = ' + KEY + '\nAllowedIPs = 192.0.2.125/32\n'
    baseline = a.persistent_fields(raw, {KEY})
    changed = a.patch_wg_config(raw, package()['wireguard'])
    restored = a.patch_wg_config(changed, package()['wireguard'], baseline)
    assert a.persistent_fields(restored, {KEY}) == baseline
    assert 'PrivateKey = PRIVATE' in restored
    assert 'MTU =' not in restored and 'Endpoint =' not in restored
