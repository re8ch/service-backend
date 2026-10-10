#!/usr/bin/env python3
"""Persist the proven LA1/R640 public-mesh peer without restarting either host.

The running WireGuard and FRR state must already have passed the canary probes.
This script owns only the LA1/R640 WireGuard configuration fields below.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


PATH = Path('/etc/wireguard/wg-public-mesh.conf')
LA1_KEY = 'UxfIwY/nAo783PFZ7X2wqk4VzIrmmAWGgoELZLUsRj8='
R640_KEY = 'kaA9Uy2hS5mhGBD41QoTtyzAQK18S4VycqAewRHK2ks='

LA1_CONFIG = f'''[Interface]
Address = 172.31.92.104/32
ListenPort = 51888
MTU = 1280
Table = off
PostUp = wg set %i private-key /etc/wireguard/wg-public-mesh.key
PostUp = ip route replace 172.31.92.128/32 dev %i scope link
PostDown = ip route del 172.31.92.128/32 dev %i || true

[Peer]
# r640 RouterOS DNAT endpoint; API VIP is admitted only from this peer
PublicKey = {R640_KEY}
AllowedIPs = 172.31.92.128/32, 10.250.0.128/32, 10.250.0.1/32
Endpoint = 223.159.210.222:51888
PersistentKeepalive = 15
'''

R640_PEER = f'''\n[Peer]
# overseas-la; public-mesh stage 10
PublicKey = {LA1_KEY}
AllowedIPs = 172.31.92.104/32, 10.250.0.104/32, 10.42.2.0/24
Endpoint = 179.255.126.104:51888
PersistentKeepalive = 15
'''

R640_ROUTE = '''PostUp = ip route replace 172.31.92.104/32 dev %i scope link
PostDown = ip route del 172.31.92.104/32 dev %i || true
'''


def desired(node, current):
    if node == 'la1':
        if current and current != LA1_CONFIG:
            raise ValueError('LA1 config exists and differs; inspect before replacing')
        return LA1_CONFIG
    if not current.startswith('[Interface]\n') or '[Peer]' not in current:
        raise ValueError('R640 base config is missing')
    if LA1_KEY in current:
        if R640_ROUTE not in current or R640_PEER.strip() not in current:
            raise ValueError('R640 peer exists with different fields')
        return current
    if re.search(r'(?m)^PostUp = ip route replace 172\.31\.92\.104/32', current):
        raise ValueError('R640 route exists without the expected peer')
    return current.replace('[Peer]', R640_ROUTE + '\n[Peer]', 1).rstrip() + '\n' + R640_PEER


def verify_host(node):
    expected = LA1_KEY if node == 'la1' else R640_KEY
    key = subprocess.check_output(['wg', 'show', 'wg-public-mesh', 'public-key'], text=True).strip()
    if key != expected:
        raise ValueError('running WireGuard interface does not match selected node')
    if node == 'la1':
        with open('/etc/wireguard/wg-public-mesh.key', 'rb') as source:
            public = subprocess.check_output(['wg', 'pubkey'], stdin=source, text=True).strip()
        if public != LA1_KEY:
            raise ValueError('LA1 host private key does not match declared public key')


def persist(content):
    PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if PATH.exists():
        backup = PATH.with_name(PATH.name + '.backup-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
        backup.write_bytes(PATH.read_bytes())
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(dir=PATH.parent, prefix='.wg-public-mesh.')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', choices=['la1', 'r640'], required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    verify_host(args.node)
    current = PATH.read_text() if PATH.exists() else ''
    planned = desired(args.node, current)
    changed = planned != current
    if args.apply and changed:
        persist(planned)
    print(f'{args.node}: {"update" if changed else "current"}; {"applied" if args.apply else "plan"}')


if __name__ == '__main__':
    main()
