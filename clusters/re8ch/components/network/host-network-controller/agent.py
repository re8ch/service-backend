#!/usr/bin/env python3
"""Host-local, crash-recoverable network transactions. No cluster API dependency.

Only explicit WG peer fields, interface MTU, and exact FRR prefix lists are
owned. Other peers, private keys, FRR neighbors and routing tables are untouched.
The signed GitOps delivery plane supplies the reviewed package and adoption file.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

STATE = Path('/var/lib/re8ch/host-network')


def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError(f'{argv[0]} failed ({p.returncode})')
    return p.stdout


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(path, value):
    atomic(path, json.dumps(value, sort_keys=True, indent=2) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(p, adoption):
    if p['node'] != adoption['node'] or p['scope'] != adoption['scope']:
        raise ValueError('node/scope not adopted')
    if not re.fullmatch('[0-9a-f]{64}', p['revision']):
        raise ValueError('immutable model digest required')
    if not 30 <= p['timeout'] <= 180:
        raise ValueError('bounded rollback deadline required')
    if p['scope'] not in ('public-mesh-peers', 'control-transit-peer'):
        raise ValueError('unsupported ownership scope')
    wg = p['wireguard']
    expected_interface = 'wg-public-mesh' if p['scope'] == 'public-mesh-peers' else {'restricted-node-abb3f27a69': 'wg-cp-mubao'}.get(p['node'])
    if wg['interface'] != expected_interface or not 1120 <= wg['mtu'] <= 1420:
        raise ValueError('invalid interface/MTU')
    seen = set()
    for peer in wg['peers']:
        if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', peer['publicKey']):
            raise ValueError('invalid public key')
        if peer['publicKey'] in seen:
            raise ValueError('duplicate peer')
        seen.add(peer['publicKey'])
        if not 5 <= peer['keepalive'] <= 120:
            raise ValueError('invalid keepalive')
        roots = ('192.0.2.246/24', '192.0.2.65/24', '192.0.2.121/24', '192.0.2.57/16')
        if p['scope'] == 'control-transit-peer':
            roots += ('192.0.2.34/28', '192.0.2.95/24', '192.0.2.240/32')
        for prefix in peer['allowedIPs']:
            n = ipaddress.ip_network(prefix)
            if not any(n.subnet_of(ipaddress.ip_network(root)) for root in
                       roots):
                raise ValueError('prefix outside fabric authority')
        if peer.get('endpoint'):
            ip, port = peer['endpoint'].rsplit(':', 1)
            ipaddress.ip_address(ip.strip('[]'))
            if not 1024 <= int(port) <= 65535:
                raise ValueError('invalid endpoint')
    if set(seen) != set(adoption['peerKeys']):
        raise ValueError('peer ownership change requires new adoption')
    if set(p['prefixLists']) != set(adoption['prefixLists']):
        raise ValueError('FRR ownership change requires new adoption')
    for name, lines in p['prefixLists'].items():
        if not re.fullmatch(r'(FROM-[0-9]+|SELF-ACCEL|SELF)', name):
            raise ValueError('unsupported FRR prefix-list')
        for line in lines:
            if not re.fullmatch(r'ip prefix-list ' + re.escape(name) + r' seq [0-9]+ (permit|deny) [0-9./]+', line):
                raise ValueError('invalid FRR prefix-list statement')
            n = ipaddress.ip_network(line.split()[-1])
            if not any(n.subnet_of(ipaddress.ip_network(root)) for root in
                       ('192.0.2.65/24', '192.0.2.121/24', '192.0.2.57/16')):
                raise ValueError('FRR prefix outside authority')
    if not p['health']['bgpPeers'] or not p['health']['checks']:
        raise ValueError('health gates required')
    # Health scripts are installed from the reviewed package, not from an API.
    for check in p['health']['checks']:
        if not re.fullmatch(r'/usr/local/lib/re8ch-host-network/checks/[a-z0-9-]+', check):
            raise ValueError('unapproved health executable')


def snapshot(p):
    wg = p['wireguard']
    interface = wg['interface']
    peers = {}
    for field in ('allowed-ips', 'endpoints', 'persistent-keepalive'):
        for line in run(['wg', 'show', interface, field]).splitlines():
            key, value = line.split('\t', 1)
            peers.setdefault(key, {})[field] = value
    owned = []
    for peer in wg['peers']:
        key = peer['publicKey']
        if key not in peers:
            raise ValueError('adoption requires existing peer')
        v = peers[key]
        if peer.get('endpoint') and v['endpoints'] == '(none)':
            raise ValueError('cannot adopt an endpoint without a restorable baseline')
        for other_key, other in peers.items():
            if other_key != key and set(peer['allowedIPs']) & set(other['allowed-ips'].split()):
                raise ValueError('AllowedIPs ownership collision')
        owned.append({'publicKey': key, 'allowedIPs': v['allowed-ips'].split(),
                      'endpoint': None if v['endpoints'] == '(none)' else v['endpoints'],
                      'keepalive': 0 if v['persistent-keepalive'] == 'off' else int(v['persistent-keepalive'])})
    conf = run(['vtysh', '-c', 'show running-config'])
    prefixes = {name: [line.strip() for line in conf.splitlines()
                      if line.startswith('ip prefix-list ' + name + ' ')] for name in p['prefixLists']}
    mtu = json.loads(run(['ip', '-j', 'link', 'show', interface]))[0]['mtu']
    persistent = Path('/etc/wireguard/' + interface + '.conf').read_text()
    scopes = persistent_fields(persistent, {p['publicKey'] for p in owned})
    return {'wireguard': {'interface': interface, 'mtu': mtu, 'peers': owned},
            'prefixLists': prefixes, 'persistentFields': scopes}


def persistent_fields(text, keys):
    result = {}
    for block in re.split(r'(?m)(?=^\[(?:Interface|Peer)\]\s*$)', text):
        match = re.search(r'(?m)^PublicKey\s*=\s*(\S+)\s*$', block)
        name = 'Interface' if block.startswith('[Interface]') else match[1] if match else None
        if name != 'Interface' and name not in keys:
            continue
        fields = ['MTU'] if name == 'Interface' else ['AllowedIPs', 'Endpoint', 'PersistentKeepalive']
        result[name] = {}
        for field in fields:
            value = re.search(r'(?m)^' + field + r'\s*=\s*(.*?)\s*$', block)
            result[name][field] = value[1] if value else None
    if set(result) != keys | {'Interface'}:
        raise ValueError('persistent adoption scope missing')
    return result


def patch_wg_config(text, wg, persistent=None):
    """Change only adopted fields; preserve keys, hooks and unowned peers."""
    blocks = re.split(r'(?m)(?=^\[(?:Interface|Peer)\]\s*$)', text)
    wanted = {p['publicKey']: p for p in wg['peers']}
    found = set()
    result = []
    for block in blocks:
        lines = block.splitlines()
        fields = {}
        if block.startswith('[Interface]'):
            fields = {'MTU': str(wg['mtu'])}
        elif block.startswith('[Peer]'):
            match = re.search(r'(?m)^PublicKey\s*=\s*(\S+)\s*$', block)
            if match and match[1] in wanted:
                peer = wanted[match[1]]
                found.add(match[1])
                fields = {'AllowedIPs': ', '.join(peer['allowedIPs']),
                          'PersistentKeepalive': str(peer['keepalive'])}
                if peer.get('endpoint'):
                    fields['Endpoint'] = peer['endpoint']
        if persistent is not None and fields:
            name = 'Interface' if block.startswith('[Interface]') else match[1]
            fields = persistent[name]
        for key, value in fields.items():
            lines = [line for line in lines if not re.match(r'^' + key + r'\s*=', line)]
            if value is not None:
                lines.append(f'{key} = {value}')
        result.append('\n'.join(lines))
    if found != set(wanted):
        raise ValueError('persistent config missing adopted peers')
    return '\n'.join(result) + '\n'


def apply_scopes(p, restore=False):
    wg = p['wireguard']
    path = Path('/etc/wireguard/' + wg['interface'] + '.conf')
    # Always merge owned fields into the latest persistent file.
    atomic(path, patch_wg_config(path.read_text(), wg, p.get('persistentFields') if restore else None))
    run(['ip', 'link', 'set', 'dev', wg['interface'], 'mtu', str(wg['mtu'])])
    for peer in wg['peers']:
        argv = ['wg', 'set', wg['interface'], 'peer', peer['publicKey'],
                'allowed-ips', ','.join(peer['allowedIPs']), 'persistent-keepalive', str(peer['keepalive'])]
        if peer.get('endpoint'):
            argv += ['endpoint', peer['endpoint']]
        run(argv)
    current = run(['vtysh', '-c', 'show running-config']).splitlines()
    argv = ['vtysh', '-c', 'configure terminal']
    changed = False
    for name, lines in p['prefixLists'].items():
        prior = [line for line in current if line.startswith('ip prefix-list ' + name + ' ')]
        desired_sequences = {line.split()[4] for line in lines}
        for line in lines:
            if line not in prior:
                argv += ['-c', line]
                changed = True
        for line in prior:
            if line.split()[4] not in desired_sequences:
                argv += ['-c', 'no ip prefix-list ' + name + ' seq ' + line.split()[4]]
                changed = True
    if changed:
        argv += ['-c', 'end', '-c', 'write memory']
        run(argv)


def syntax_check(p):
    wg = p['wireguard']
    candidate = patch_wg_config(Path('/etc/wireguard/' + wg['interface'] + '.conf').read_text(), wg)
    # Host confinement may deny wg-quick reading /tmp even for root. Stage
    # within each tool's configuration directory, mode 0600, and always remove.
    staged = []
    try:
        fd, filename = tempfile.mkstemp(prefix='hn-', suffix='.conf', dir='/etc/wireguard')
        os.close(fd)
        path = Path(filename)
        staged.append(path)
        atomic(path, candidate)
        run(['wg-quick', 'strip', str(path)])
        conf = run(['vtysh', '-c', 'show running-config'])
        conf = '\n'.join(line for line in conf.splitlines()
                         if line.strip() not in ('Building configuration...', 'Current configuration:')
                         and not any(line.startswith('ip prefix-list ' + name + ' ') for name in p['prefixLists']))
        conf += '\n' + '\n'.join(line for lines in p['prefixLists'].values() for line in lines) + '\n'
        fd, filename = tempfile.mkstemp(prefix='hn-', suffix='.conf', dir='/etc/frr')
        os.close(fd)
        path = Path(filename)
        staged.append(path)
        atomic(path, conf)
        run(['vtysh', '-C', '-f', str(path)])
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def healthy(p):
    summary = json.loads(run(['vtysh', '-c', 'show bgp ipv4 unicast summary json']))
    for peer in p['health']['bgpPeers']:
        if summary.get('peers', {}).get(peer, {}).get('state') != 'Established':
            return False
    bfd = json.loads(run(['vtysh', '-c', 'show bfd peers json'])) if p['health'].get('bfdPeers') else []
    if any(not any(x.get('peer') == peer and x.get('status') == 'up' for x in bfd)
           for peer in p['health'].get('bfdPeers', [])):
        return False
    handshakes = dict(line.split('\t') for line in run(['wg', 'show', p['wireguard']['interface'], 'latest-handshakes']).splitlines())
    if any(time.time() - int(handshakes.get(peer['publicKey'], 0)) > 180 for peer in p['wireguard']['peers']):
        return False
    for check in p['health']['checks']:
        run([check])
    return True


def recover(state, force=False):
    path = state / 'transaction.json'
    if not path.exists():
        return
    t = json.loads(path.read_text())
    if t['state'] not in ('applying', 'verifying', 'rolling-back'):
        return
    if not force and time.time() < t['deadline']:
        return
    t['state'] = 'rolling-back'
    save(path, t)
    try:
        apply_scopes(t['baseline'], restore=True)
    except Exception:
        # Retry rollback, but never retry the failed forward revision.
        save(state / 'blocked.json', {'digest': t['digest'], 'reason': 'rollback-failed'})
        raise
    t['state'] = 'rolled-back'
    save(path, t)
    save(state / 'blocked.json', {'digest': t['digest'], 'reason': 'health-or-deadline'})
    save(state / 'status.json', {'state': 'rolled-back', 'digest': t['digest'], 'observedAt': time.time()})


def reconcile(state, p, adoption):
    validate(p, adoption)
    recover(state)
    target = digest(p)
    if (state / 'blocked.json').exists() and json.loads((state / 'blocked.json').read_text())['digest'] == target:
        return 'blocked'
    txpath = state / 'transaction.json'
    if txpath.exists() and json.loads(txpath.read_text())['state'] in ('applying', 'verifying', 'rolling-back'):
        return 'pending'
    baseline = snapshot(p)
    expected = {'wireguard': p['wireguard'], 'prefixLists': p['prefixLists']}
    current = {key: baseline[key] for key in expected}
    persistent = baseline.get('persistentFields', {})
    persistent_ok = persistent.get('Interface', {}).get('MTU') == str(p['wireguard']['mtu'])
    for peer in p['wireguard']['peers']:
        fields = persistent.get(peer['publicKey'], {})
        persistent_ok = persistent_ok and sorted(x.strip() for x in (fields.get('AllowedIPs') or '').split(',')) == sorted(peer['allowedIPs'])
        persistent_ok = persistent_ok and fields.get('PersistentKeepalive') == str(peer['keepalive'])
        if peer.get('endpoint'):
            persistent_ok = persistent_ok and fields.get('Endpoint') == peer['endpoint']
    if (state / 'committed.json').exists() and json.loads((state / 'committed.json').read_text())['digest'] == target and current == expected and persistent_ok:
        return 'converged'
    try:
        if not healthy(p):
            raise RuntimeError('BGP/BFD/handshake gate')
    except Exception as exc:
        save(state / 'status.json', {'state': 'preflight-unhealthy', 'digest': target,
             'reason': str(exc), 'observedAt': time.time()})
        return 'preflight-unhealthy'
    syntax_check(p)
    # Journal is durable before the first write. The independently installed
    # systemd watchdog can restore it after SIGKILL, reboot or loss of delivery.
    t = {'state': 'applying', 'digest': target, 'revision': p['revision'],
         'deadline': time.time() + p['timeout'], 'baseline': baseline}
    save(txpath, t)
    try:
        apply_scopes(p)
        t['state'] = 'verifying'
        save(txpath, t)
        consecutive = 0
        while time.time() < t['deadline'] - 10:
            consecutive = consecutive + 1 if healthy(p) else 0
            if consecutive >= 3:
                t['state'] = 'committed'
                save(txpath, t)
                save(state / 'committed.json', {'digest': target, 'package': p})
                save(state / 'status.json', {'state': 'committed', 'digest': target, 'observedAt': time.time()})
                return 'committed'
            time.sleep(3)
        raise RuntimeError('health deadline')
    except Exception:
        recover(state, force=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['reconcile', 'watchdog', 'recover-boot'])
    parser.add_argument('--state', type=Path, default=STATE)
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.state, 0o700)
    with (args.state / 'lock').open('a') as lock:
        # All forward commands have a bound; watchdog acquires after a crashed
        # worker or at the next tick. No two host writers enter this transaction.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if args.action in ('watchdog', 'recover-boot'):
            recover(args.state, force=args.action == 'recover-boot')
        else:
            print(reconcile(args.state, json.loads((args.state / 'desired.json').read_text()),
                            json.loads((args.state / 'adoption.json').read_text())))


if __name__ == '__main__':
    main()
