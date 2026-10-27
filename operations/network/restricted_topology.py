#!/usr/bin/env python3
"""Resolve pinned topology privately; public checks never need production inputs."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'contracts/network-topology/source.json'
MAX_BYTES = 512 * 1024
REFERENCES = {
    'operations/network/manifests/network/public-mesh/inventory.json': 'operations',
    'clusters/re8ch/components/network/public-mesh/inventory.json': 'runtime',
    'clusters/re8ch/components/network/podcidr-fabric/public-mesh-inventory.json': 'runtime',
}
ENCRYPTED = (
    'clusters/re8ch/secrets/restricted-topology-context.sops.yaml',
    'clusters/re8ch/components/network/podcidr-fabric/public-mesh-podcidr.sops.yaml',
)


def source():
    return json.loads((ROOT / SOURCE).read_text())


def load_bundle():
    encoded = os.environ.get('NETWORK_TOPOLOGY_BUNDLE')
    path = os.environ.get('NETWORK_TOPOLOGY_FILE')
    if encoded:
        if len(encoded) > 48 * 1024:
            raise ValueError('Restricted source too large')
        with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
            raw = stream.read(MAX_BYTES + 1)
    elif path:
        path = Path(path).resolve()
        if path.is_relative_to(ROOT) or path.stat().st_size > MAX_BYTES:
            raise ValueError('Private source must be outside the checkout')
        raw = path.read_bytes()
    else:
        raise ValueError('Restricted topology is unavailable; use the authenticated platform API or protected topology workflow')
    if len(raw) > MAX_BYTES or 'sha256:' + hashlib.sha256(raw).hexdigest() != source()['sourceDigest']:
        raise ValueError('Restricted source version mismatch')
    document = json.loads(raw)
    if set(document) != {'schemaVersion', 'publicMesh', 'runtimeProjection', 'context'} or document['schemaVersion'] != 1:
        raise ValueError('Invalid restricted topology schema')
    return document


def resolve_reference(document):
    if document.get('kind') != 'RestrictedTopologyReference':
        return document  # Explicit synthetic fixtures and private operator inputs.
    if set(document) != {'kind', 'source', 'sourceDigest', 'view'} or document['source'] != SOURCE or document['sourceDigest'] != source()['sourceDigest']:
        raise ValueError('Invalid topology reference')
    bundle = load_bundle()
    if document['view'] == 'operations':
        return bundle['publicMesh']
    if document['view'] == 'projection':
        return bundle['runtimeProjection']
    if document['view'] == 'runtime':
        from fabric.fabric_model import project
        return project(bundle['publicMesh'], bundle['runtimeProjection'])
    raise ValueError('Invalid topology view')


def load_inventory(path):
    return resolve_reference(json.loads(Path(path).read_text()))


def verify_public():
    pin = source()
    for relative, view in REFERENCES.items():
        document = json.loads((ROOT / relative).read_text())
        if document != {'kind': 'RestrictedTopologyReference', 'source': SOURCE, 'sourceDigest': pin['sourceDigest'], 'view': view}:
            raise ValueError('Plaintext or stale inventory reference')
    model = json.loads((ROOT / 'operations/network/fabric/model.json').read_text())
    for value, view in ((model['sections']['publicMesh'], 'operations'), (model['runtimeProjections']['publicMesh'], 'projection')):
        if value != {'kind':'RestrictedTopologyReference', 'source':SOURCE, 'sourceDigest':pin['sourceDigest'], 'view':view}:
            raise ValueError('Plaintext or stale model section')
    for relative in ENCRYPTED:
        document = yaml.safe_load((ROOT / relative).read_text())
        if document.get('kind') != 'ConfigMap' or not document.get('sops') or document['metadata']['annotations'].get('operations.re8ch.com/source-digest') != pin['sourceDigest']:
            raise ValueError('Invalid encrypted projection')
        if not document.get('data') or not all(isinstance(value, str) and value.startswith('ENC[AES256_GCM,') for value in document['data'].values()):
            raise ValueError('Plaintext topology projection')


def validate_private():
    verify_public()
    load_bundle()
    from fabric.fabric_model import load_model, validate
    for relative, expected in validate(load_model()).items():
        if load_inventory(ROOT / relative) != expected:
            raise ValueError('Fabric projection drift')
    # Production-fixture regressions run only with protected inputs; pytest output
    # is captured because assertion diffs can contain full private documents.
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
        'tests/test_network_fabric_model.py', 'tests/test_public_mesh_config.py',
        'tests/test_r640_public_authority.py'], cwd=ROOT, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('Restricted topology regression validation failed')


def runtime_check():
    bundle = load_bundle()
    # Reuse the existing source-verified API/CA custody and read-only OIDC identity.
    spec = importlib.util.spec_from_file_location('contract_bundle', ROOT / 'operations/infrastructure-contract/bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = module.decode(os.environ['CONTRACT_BUNDLE'])
    if module.digest(files) != module.expected_digest():
        raise ValueError('API configuration version mismatch')
    api = yaml.safe_load(files['cluster.yaml'])['spec']['api']
    parts = urllib.parse.urlsplit(os.environ['ACTIONS_ID_TOKEN_REQUEST_URL'])
    query = dict(urllib.parse.parse_qsl(parts.query))
    query['audience'] = 'https://' + api['publicFQDN'] + '/observer'
    request = urllib.request.Request(urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query))),
        headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)['value']
    ref = source()['runtimeRef']
    url = 'https://{}:{}/api/v1/namespaces/{}/configmaps/{}'.format(api['publicFQDN'], api['publicPort'], ref['namespace'], ref['name'])
    tls = ssl.create_default_context(cadata=os.environ['KUBE_API_CA'])
    def get(endpoint, authenticated=True):
        request = urllib.request.Request(endpoint, headers={'Authorization': 'Bearer ' + token} if authenticated else {})
        with urllib.request.urlopen(request, context=tls, timeout=30) as response:
            return json.load(response)
    for endpoint, authenticated, codes in ((url, False, (401, 403)), (url + '-denied-probe', True, (403,))):
        try:
            get(endpoint, authenticated)
        except urllib.error.HTTPError as error:
            if error.code not in codes:
                raise ValueError('Unexpected authorization probe result') from None
        else:
            raise ValueError('Runtime topology access is not bounded')
    data = get(url)['data']
    if set(data) != {'bundle.json', 'context.md'} or 'sha256:' + hashlib.sha256(data['bundle.json'].encode()).hexdigest() != source()['sourceDigest'] or json.loads(data['bundle.json']) != bundle or data['context.md'] != bundle['context']:
        raise ValueError('Runtime topology projection mismatch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check-public', 'validate', 'runtime-check'])
    args = parser.parse_args()
    try:
        {'check-public': verify_public, 'validate': validate_private, 'runtime-check': runtime_check}[args.action]()
    except Exception:
        raise SystemExit('Restricted topology check failed; inspect through the authenticated platform interface') from None
    print(json.dumps({'check': args.action, 'result': 'passed'}))


if __name__ == '__main__':
    main()
