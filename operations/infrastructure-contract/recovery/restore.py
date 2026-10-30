"""One-shot fixed Secret recovery; private material stays inside pod memory."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

RECIPIENT = 'age1c7camhc8ql85t5u4hrjmsmkurfa0n0v7lg05espasa9fqapjz5vqql8lfj'
SOURCE = 'sha256:99c7f741aec926fe7e3839adf488d58c6fb7522d73b42fbd820a9bdbf8fbdc64'
OWNER = 'infra-contracts-recovery-20261030'
TARGETS = {'ai-operations-ssh-config': {'ssh-config', 'byoc-known-hosts'},
           'infrastructure-contracts': {'compat-nodes.yaml'}}
KEY = '/age/age.agekey'
CIPHER_HASHES = {'ai-operations-ssh-config': 'ba5c7754c495e4c7f96dc90a0392a34298ec2c8980191cee518143d3d3992509', 'infrastructure-contracts': '5e88cf065289f24a53f54fdf8568846d2cdf9ca2f1e76eb12f02e5c88499f89c'}
class Rejected(Exception): pass
class DiagnosticRejected(Rejected):
    def __init__(self, stage):
        self.stage = stage
        super().__init__()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise Rejected()

def sops(args, body, runner=subprocess.run):
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
           'HOME': '/tmp', 'SOPS_AGE_KEY_FILE': KEY}
    r = runner(['sops', *args], input=body, stdout=subprocess.PIPE,
               stderr=subprocess.DEVNULL, env=env, cwd='/tmp', timeout=30, check=False)
    if r.returncode or len(r.stdout) > 1048576: raise Rejected()
    return r.stdout

def verify_key():
    # Challenge is noncredential random data, discarded without printing.
    body = json.dumps({'challenge': secrets.token_hex(32)}).encode()
    encrypted = sops(['encrypt', '--age', RECIPIENT, '--input-type', 'json', '--output-type', 'json', '/dev/stdin'], body)
    plain = sops(['decrypt', '--input-type', 'json', '--output-type', 'json', '/dev/stdin'], encrypted)
    if json.loads(plain) != json.loads(body): raise Rejected()

def decode(name, ciphertext):
    try:
        c = json.loads(ciphertext)
        if [a['recipient'] for a in c['sops']['age']] != [RECIPIENT]: raise Rejected()
    except Exception:
        raise DiagnosticRejected('source_envelope') from None
    try:
        plain = sops(['decrypt', '--input-type', 'json', '--output-type', 'json', '/dev/stdin'], ciphertext)
    except Exception:
        raise DiagnosticRejected('source_sops_decrypt') from None
    try:
        value = json.loads(plain)
        if value.get('apiVersion') != 'v1' or value.get('kind') != 'Secret': raise Rejected()
        m = value['metadata']
        if m['name'] != name or m['namespace'] != 'ai-operations' or m['annotations']['operations.re8ch.com/source-digest'] != SOURCE: raise Rejected()
        data = value.get('stringData')
        if not isinstance(data, dict) or set(data) != TARGETS[name] or 'data' in value: raise Rejected()
        if any(not isinstance(v, str) or not v for v in data.values()): raise Rejected()
        return {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
    except Exception:
        raise DiagnosticRejected('source_document_validation') from None

def patch(name, data, receipt):
    if set(receipt) != {'uid', 'resourceVersion'} or any(not isinstance(v, str) or not v or v.startswith('REPLACE') for v in receipt.values()): raise Rejected()
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': receipt['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': receipt['resourceVersion']},
        {'op': 'test', 'path': '/metadata/labels/operations.re8ch.com~1recovery', 'value': OWNER},
        {'op': 'add', 'path': '/data', 'value': data},
        {'op': 'add', 'path': '/metadata/annotations/operations.re8ch.com~1source-digest', 'value': SOURCE},
    ]

def send(name, ops, dry_run):
    host = os.environ['KUBERNETES_SERVICE_HOST']
    port = os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
    if not host or not port.isdigit(): raise Rejected()
    if ':' in host: host = '[' + host + ']'
    path = '/api/v1/namespaces/ai-operations/secrets/' + name
    url = f'https://{host}:{port}{path}' + ('?dryRun=All' if dry_run else '')
    sa = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    token = (sa / 'token').read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(sa / 'ca.crt'))))
    req = urllib.request.Request(url, data=json.dumps(ops).encode(), method='PATCH',
          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json-patch+json'})
    with opener.open(req, timeout=30) as response:
        raw = response.read(1048577)
    if len(raw) > 1048576: raise Rejected()
    return json.loads(raw)

def prepare(directory, state):
    ciphertexts = {}
    for name in TARGETS:
        try:
            raw = (directory / (name + '.json')).read_bytes()
            if len(raw) > 1048576 or hashlib.sha256(raw).hexdigest() != CIPHER_HASHES[name]: raise Rejected()
            ciphertexts[name] = raw
        except Exception:
            raise DiagnosticRejected('source_cipher_hash') from None
    try:
        verify_key()
    except Exception:
        raise DiagnosticRejected('key_challenge') from None
    decoded = {}
    for name, raw in ciphertexts.items():
        try:
            decoded[name] = decode(name, raw)
        except DiagnosticRejected:
            raise
        except Exception:
            raise DiagnosticRejected('source_decrypt_and_validate') from None
    try:
        fd = os.open(state / 'prepared.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump({'sourceDigest': SOURCE, 'data': decoded}, out)
    except Exception:
        raise DiagnosticRejected('memory_handoff') from None
    return {'prepared': True, 'recipientMatched': True, 'sourceDigest': SOURCE}

def run(directory, dry_run=True, state=Path('/state')):
    receipts = json.loads((directory / 'targets.json').read_text())
    if set(receipts) != set(TARGETS): raise Rejected()
    raw = (state / 'prepared.json').read_bytes()
    if len(raw) > 1048576: raise Rejected()
    document = json.loads(raw)
    if document.get('sourceDigest') != SOURCE or set(document.get('data', {})) != set(TARGETS): raise Rejected()
    prepared = {}
    for name, data in document['data'].items():
        if not isinstance(data, dict) or set(data) != TARGETS[name]: raise Rejected()
        if any(not isinstance(v, str) or not base64.b64decode(v, validate=True) for v in data.values()): raise Rejected()
        prepared[name] = (data, patch(name, data, receipts[name]))
    result = []
    # Preflight both objects before any real write. No automatic retries.
    for name, (data, ops) in prepared.items():
        returned = send(name, ops, True)
        if returned.get('data') != data or returned['metadata']['uid'] != receipts[name]['uid']: raise Rejected()
    if not dry_run:
        for name, (data, ops) in prepared.items():
            returned = send(name, ops, False)
            if returned.get('data') != data or returned['metadata']['uid'] != receipts[name]['uid']: raise Rejected()
    for name, (data, _) in prepared.items():
        result.append({'reference': 'kubernetes://ai-operations/' + name, 'matched': True,
                       'contentHash': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()})
    return {'dryRun': dry_run, 'sourceDigest': SOURCE, 'recipientMatched': True, 'secrets': result}

def main():
    try:
        if sys.argv[1:] not in ([], ['--apply'], ['--prepare']): raise Rejected()
        result = prepare(Path('/input'), Path('/state')) if sys.argv[1:] == ['--prepare'] else run(Path('/input'), dry_run=not sys.argv[1:])
        print(json.dumps(result))
    except DiagnosticRejected as error:
        print(json.dumps({'status': 'failed', 'stage': error.stage, 'reason': 'recovery_not_confirmed_do_not_retry'}))
        return 1
    except Exception:
        print('{"status":"failed","reason":"recovery_not_confirmed_do_not_retry"}')
        return 1
    return 0
if __name__ == '__main__': sys.exit(main())
