#!/usr/bin/env python3
"""Materialize the existing Environment Secret without exporting it to logs."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
MEMBERS = ('cluster.yaml', 'nodes.yaml', 'targets.yaml', 'observation-policy.yaml')
MAX_BYTES = 2 * 1024 * 1024


def decode(value):
    if not value or len(value) > MAX_BYTES:
        raise ValueError('bundle size')
    raw = base64.b64decode(value, validate=True)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            if member.name not in MEMBERS or not member.isfile() or member.name in files:
                raise ValueError('bundle member')
            if member.size > MAX_BYTES or sum(map(len, files.values())) + member.size > MAX_BYTES:
                raise ValueError('bundle size')
            files[member.name] = archive.extractfile(member).read()
    if set(files) != set(MEMBERS):
        raise ValueError('bundle incomplete')
    return files


def digest(files):
    result = hashlib.sha256()
    for name in MEMBERS:
        result.update(name.encode() + b'\0' + files[name] + b'\0')
    return 'sha256:' + result.hexdigest()


def expected_digest():
    values = set()
    for name in ('infrastructure-contracts', 'cluster-node-inventory', 'ai-operations-inventory'):
        path = ROOT / 'clusters/re8ch/secrets' / (name + '.sops.yaml')
        for document in yaml.safe_load_all(path.read_text()):
            if document:
                values.add(document['metadata']['annotations']['operations.re8ch.com/source-digest'])
    if len(values) != 1:
        raise ValueError('inconsistent source pins')
    return values.pop()


def materialize(value, output, expected):
    files = decode(value)
    if digest(files) != expected:
        raise ValueError('source version mismatch')
    output = Path(output)
    # Never permit plaintext to enter a checkout, including ignored paths.
    if output.resolve().is_relative_to(ROOT) or output.exists():
        raise ValueError('private output must be new and outside the checkout')
    output.mkdir(mode=0o700)
    try:
        for name, data in files.items():
            descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
        with tempfile.TemporaryDirectory(prefix='contract-render-') as rendered:
            env = {**os.environ, 'INFRASTRUCTURE_CONTRACT_DIR': str(output), 'INFRASTRUCTURE_OUTPUT_DIR': rendered}
            for command in ('render', 'check'):
                result = subprocess.run([sys.executable, str(ROOT / 'operations/infrastructure-contract/compiler.py'), command],
                                        env=env, capture_output=True, timeout=60)
                if result.returncode:
                    raise ValueError('contract validation')
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        expected = expected_digest()
        materialize(os.environ.pop('CONTRACT_BUNDLE', ''), args.output, expected)
        print(json.dumps({'schema': 1, 'sourceDigest': expected, 'validation': 'passed'}))
    except Exception:
        # YAML/schema exceptions can include complete documents and addresses.
        print('Restricted configuration validation failed; no configuration values are emitted.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
