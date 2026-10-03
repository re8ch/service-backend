#!/usr/bin/env python3
"""Bounded fabric gate. Inputs are a GitOps-declared health target set."""
import concurrent.futures
import json
from pathlib import Path
import socket
import ssl
import struct
import time
import urllib.request


def dns(ip, tcp, source):
    ident = 12753
    question = b''.join(bytes([len(p)]) + p.encode() for p in 'kubernetes.default.svc.cluster.local'.split('.')) + b'\0\0\1\0\1'
    request = struct.pack('!HHHHHH', ident, 256, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        s.bind((source, 0))
        s.connect((ip, 53))
        s.sendall(struct.pack('!H', len(request)) + request if tcp else request)
        if tcp:
            def recv(n):
                data = b''
                while len(data) < n:
                    part = s.recv(n-len(data))
                    if not part:
                        raise RuntimeError('short DNS response')
                    data += part
                return data
            answer = recv(struct.unpack('!H', recv(2))[0])
        else:
            answer = s.recv(4096)
    if len(answer) < 12 or struct.unpack('!H', answer[:2])[0] != ident or not answer[2] & 128 or answer[3] & 15:
        raise RuntimeError('DNS failed')


def tcp(target):
    with socket.create_connection((target['ip'], target['port']), timeout=3):
        pass


def main():
    p = json.loads(Path('/var/lib/re8ch/host-network/desired.json').read_text())
    h = p['health']
    discovery = json.loads(Path('/var/lib/re8ch/host-network/health-targets.json').read_text())
    if time.time() - discovery['observedAt'] > 120:
        raise RuntimeError('stale DNS discovery')
    h['dnsReplicas'] = discovery['dnsReplicas']
    if len(h['dnsReplicas']) < 4 or not h['tcpTargets']:
        raise RuntimeError('incomplete health target set')
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        jobs = [pool.submit(dns, ip, protocol, h['sourceIdentity']) for ip in h['dnsReplicas'] for protocol in (False, True)]
        jobs += [pool.submit(tcp, target) for target in h['tcpTargets']]
        for job in jobs:
            job.result()
    # External origin/certificate and explicit proxy are declared separately.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': h['egressProxy']}))
    with opener.open(h['egressURL'], timeout=5) as response:
        if response.status not in (200, 204):
            raise RuntimeError('egress failure')


if __name__ == '__main__':
    main()
