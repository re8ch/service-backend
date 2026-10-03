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
                {'publicKey': KEY, 'allowedIPs': ['172.31.92.128/32', '10.250.0.128/32'], 'endpoint': '1.2.3.4:51820', 'keepalive': 15}]},
            'prefixLists': {'FROM-128': ['ip prefix-list FROM-128 seq 10 permit 10.250.0.128/32']},
            'health': {'bgpPeers': ['172.31.92.128'], 'checks': ['/usr/local/lib/re8ch-host-network/checks/fabric']}}


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
    raw = '[Interface]\nPrivateKey = HOST-ONLY\nPostUp = keep-this\nMTU = 1420\n\n[Peer]\nPublicKey = ' + KEY + '\nAllowedIPs = 172.31.92.128/32\n\n[Peer]\nPublicKey = OTHER\nAllowedIPs = 10.8.0.0/16\n'
    patched = a.patch_wg_config(raw, package()['wireguard'])
    assert 'PrivateKey = HOST-ONLY' in patched
    assert 'PostUp = keep-this' in patched
    assert '[Peer]\nPublicKey = OTHER\nAllowedIPs = 10.8.0.0/16' in patched
    assert 'MTU = 1280' in patched and 'MTU = 1420' not in patched
    assert patched == a.patch_wg_config(patched, package()['wireguard'])


def test_apply_failure_rolls_back_and_blocks_revision(tmp_path, monkeypatch):
    p = package()
    baseline = {'wireguard': {}, 'prefixLists': {}}
    monkeypatch.setattr(a, 'snapshot', lambda p: baseline)
    monkeypatch.setattr(a, 'healthy', lambda p, **kw: True)
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
    monkeypatch.setattr(a, 'healthy', lambda p, **kw: False)
    assert a.reconcile(tmp_path, package(), adoption()) == 'preflight-unhealthy'
    assert not (tmp_path / 'transaction.json').exists()


def test_rollback_restores_persistent_fields_separately():
    raw = '[Interface]\nPrivateKey = PRIVATE\n\n[Peer]\nPublicKey = ' + KEY + '\nAllowedIPs = 172.31.92.128/32\n'
    baseline = a.persistent_fields(raw, {KEY})
    changed = a.patch_wg_config(raw, package()['wireguard'])
    restored = a.patch_wg_config(changed, package()['wireguard'], baseline)
    assert a.persistent_fields(restored, {KEY}) == baseline
    assert 'PrivateKey = PRIVATE' in restored
    assert 'MTU =' not in restored and 'Endpoint =' not in restored


def test_commit_requires_three_health_rounds(tmp_path, monkeypatch):
    p = package()
    calls = []
    monkeypatch.setattr(a, 'snapshot', lambda p: {'wireguard': {}, 'prefixLists': {}})
    monkeypatch.setattr(a, 'healthy', lambda p, **kw: calls.append('health') or True)
    monkeypatch.setattr(a, 'syntax_check', lambda p: None)
    monkeypatch.setattr(a, 'apply_scopes', lambda p: calls.append('apply'))
    monkeypatch.setattr(a.time, 'sleep', lambda n: None)
    assert a.reconcile(tmp_path, p, adoption()) == 'committed'
    assert calls == ['health', 'apply', 'health', 'health', 'health']
    assert json.loads((tmp_path / 'transaction.json').read_text())['state'] == 'committed'


def test_syntax_staging_uses_host_config_dirs_and_strips_cli_headers(tmp_path, monkeypatch):
    p = package()
    raw = '[Interface]\nMTU = 1280\n[Peer]\nPublicKey = ' + KEY + '\nAllowedIPs = 172.31.92.128/32\n'
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs: raw if str(path) == '/etc/wireguard/wg-public-mesh.conf' else original_read(path, *args, **kwargs))
    original_mkstemp = a.tempfile.mkstemp
    destinations = []
    def staged(**kwargs):
        destinations.append(kwargs['dir'])
        return original_mkstemp(prefix='hn-', suffix='.conf', dir=tmp_path)
    monkeypatch.setattr(a.tempfile, 'mkstemp', staged)
    def run(argv):
        if argv == ['vtysh', '-c', 'show running-config']:
            return 'Building configuration...\n\nCurrent configuration:\nfrr defaults traditional\n!\n'
        if argv[:2] == ['vtysh', '-C']:
            config = original_read(Path(argv[-1]))
            assert 'Building configuration' not in config
            assert 'Current configuration' not in config
            assert 'ip prefix-list FROM-128' in config
        return ''
    monkeypatch.setattr(a, 'run', run)
    a.syntax_check(p)
    assert '/etc/wireguard' in destinations and '/etc/frr' in destinations
    assert list(tmp_path.iterdir()) == []


def test_control_transit_package_is_scoped_to_adopted_mubao_peer():
    root = Path(__file__).parents[1] / 'clusters/re8ch/components/network/host-network-controller'
    p = json.loads((root / 'control-package.json').read_text())
    adoption = json.loads((root / 'control-adoption.json').read_text())
    a.validate(p, adoption)
    p['wireguard']['interface'] = 'wg-pt-mubao'
    with pytest.raises(ValueError, match='interface'):
        a.validate(p, adoption)


def test_control_owner_delivery_matches_reviewed_source():
    root = Path(__file__).parents[1]
    assert (root / 'operations/network/scripts/routeros/control_pod_mesh.py').read_bytes() == (
        root / 'clusters/re8ch/components/network/host-network-controller/control_pod_mesh.py').read_bytes()


def test_health_does_not_require_bfdd_for_a_non_bfd_peer(monkeypatch):
    p = package()
    p['health']['bfdPeers'] = []
    calls = []
    def run(argv):
        calls.append(argv)
        if 'show bgp ipv4 unicast summary json' in argv:
            return json.dumps({'peers': {'172.31.92.128': {'state': 'Established'}}})
        if argv[0] == 'wg':
            return KEY + '\t' + str(int(a.time.time()))
        if 'show bfd peers json' in argv:
            raise RuntimeError('bfdd not configured')
        return ''
    monkeypatch.setattr(a, 'run', run)
    assert a.healthy(p)
    assert not any('show bfd peers json' in argv for argv in calls)


def control_package():
    root = Path(__file__).parents[1] / 'clusters/re8ch/components/network/host-network-controller'
    return (json.loads((root / 'control-package.json').read_text()),
            json.loads((root / 'control-adoption.json').read_text()))


def test_extra_interface_requires_exact_reviewed_adoption():
    p, adopted = control_package()
    a.validate(p, adopted)
    extra = p['additionalWireguard'][0]
    assert {prefix for peer in extra['peers'] for prefix in peer['allowedIPs'] if prefix.startswith('10.42.')} == {'10.42.4.0/24', '10.42.3.0/24'}
    adopted['additionalPeerKeys']['wg-public-mesh'] = []
    with pytest.raises(ValueError, match='ownership'):
        a.validate(p, adopted)


def test_snapshot_journals_both_interfaces_and_persistent_fields(monkeypatch):
    p, _ = control_package()
    byiface = {w['interface']: w for w in [p['wireguard']] + p['additionalWireguard']}
    def run(argv):
        if argv[0] == 'wg':
            w = byiface[argv[2]]
            def value(peer):
                return {'allowed-ips': ' '.join(peer['allowedIPs']), 'endpoints': peer['endpoint'],
                        'persistent-keepalive': str(peer['keepalive'])}[argv[3]]
            return '\n'.join(peer['publicKey'] + '\t' + value(peer) for peer in w['peers'])
        if argv[0] == 'ip':
            return json.dumps([{'mtu': byiface[argv[-1]]['mtu']}])
        return ''
    def read(path):
        w = byiface[path.stem]
        return '[Interface]\nPrivateKey = KEEP-PRIVATE\nMTU = '+str(w['mtu'])+'\n'+''.join(
            '[Peer]\nPublicKey = '+peer['publicKey']+'\nAllowedIPs = '+', '.join(peer['allowedIPs'])+'\n'
            for peer in w['peers'])
    monkeypatch.setattr(a, 'run', run)
    monkeypatch.setattr(Path, 'read_text', read)
    baseline = a.snapshot(p)
    assert baseline['wireguard'] == p['wireguard']
    assert baseline['additionalWireguard'] == p['additionalWireguard']
    assert 'wg-public-mesh' in baseline['additionalPersistentFields']
    assert 'KEEP-PRIVATE' not in json.dumps(baseline)


def test_additional_peer_handshake_is_a_health_gate(monkeypatch):
    p, _ = control_package()
    def run(argv):
        if argv[0] == 'vtysh':
            return json.dumps({'peers': {ip: {'state': 'Established'} for ip in p['health']['bgpPeers']}})
        if argv[:3] == ['wg', 'show', 'wg-cp-mubao']:
            return p['wireguard']['peers'][0]['publicKey']+'\t'+str(int(a.time.time()))
        if argv[:3] == ['wg', 'show', 'wg-public-mesh']:
            return '\n'.join(peer['publicKey']+'\t0' for peer in p['additionalWireguard'][0]['peers'])
        raise AssertionError('health scripts must not run after failed handshake')
    monkeypatch.setattr(a, 'run', run)
    assert not a.healthy(p)
