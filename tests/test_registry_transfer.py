"""Content proof rejects corrupted/partial OCI storage without any credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1]
CHECK = ROOT / 'operations/mcp-conformance/verify-image-evidence.py'


def test_cold_layout_verifies_bytes_and_descriptor_graph(tmp_path):
    folder = tmp_path / 'cold'
    blobs = folder / 'blobs' / 'sha256'
    blobs.mkdir(parents=True)
    layer = b'nonsecret-complete-layer' * 17
    def store(value):
        digest = hashlib.sha256(value).hexdigest()
        (blobs / digest).write_bytes(value)
        return {'digest': 'sha256:' + digest, 'size': len(value)}
    descriptor = store(layer)
    descriptor['mediaType'] = 'application/octet-stream'
    manifest = {'schemaVersion': 2, 'layers': [descriptor]}
    root = store(json.dumps(manifest).encode())
    root['mediaType'] = 'application/vnd.oci.image.manifest.v1+json'
    (folder / 'index.json').write_text(json.dumps({'manifests': [root]}))
    command = [sys.executable, str(CHECK), 'layout', str(folder), root['digest']]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['allBlobHashesVerified']
    assert json.loads(result.stdout)['verifiedDescriptors'] == 2
    # Same filename/digest claim, truncated body: never trust cached metadata.
    (blobs / descriptor['digest'][7:]).write_bytes(layer[:-3])
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0 and 'digest mismatch' in result.stderr
    (blobs / descriptor['digest'][7:]).write_bytes(layer)
    (blobs / descriptor['digest'][7:]).unlink()
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0 and 'missing or wrong size' in result.stderr


def test_copied_signature_does_not_replace_destination_repository_claim(tmp_path):
    digest = 'sha256:' + 'a' * 64
    source, target = 'ghcr.io/re8ch/source', 'registry.re8ch.com/image/target'
    def payload(repo):
        return {'critical': {'identity': {'docker-reference': repo}, 'image': {'docker-manifest-digest': digest}}}
    file = tmp_path / 'verified-signatures.json'
    command = [sys.executable, str(CHECK), 'claim', str(file), digest, source, target]
    file.write_text(json.dumps([payload(source)]))
    assert subprocess.run(command, capture_output=True).returncode != 0
    file.write_text(json.dumps([payload(source), payload(target)]))
    assert subprocess.run(command, capture_output=True).returncode == 0
    file.write_text(json.dumps([payload(source), {'critical': {'identity': {'docker-reference': target}, 'image': {'docker-manifest-digest': 'sha256:' + 'b' * 64}}}]))
    assert subprocess.run(command, capture_output=True).returncode != 0
