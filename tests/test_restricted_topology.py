import base64
import gzip
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'operations/network'))
import restricted_topology as topology


@pytest.fixture
def private_source(tmp_path, monkeypatch):
    fake_root = tmp_path / 'checkout'
    source = fake_root / topology.SOURCE
    source.parent.mkdir(parents=True)
    bundle = {'schemaVersion': 1, 'publicMesh': {'nodes': [{'name': 'synthetic-peer'}]},
              'runtimeProjection': {}, 'context': 'synthetic private context'}
    raw = json.dumps(bundle, sort_keys=True, separators=(',', ':')).encode()
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    source.write_text(json.dumps({'sourceDigest': digest}))
    monkeypatch.setattr(topology, 'ROOT', fake_root)
    monkeypatch.delenv('NETWORK_TOPOLOGY_FILE', raising=False)
    monkeypatch.setenv('NETWORK_TOPOLOGY_BUNDLE', base64.b64encode(gzip.compress(raw)).decode())
    return fake_root, bundle, raw, digest


def test_versioned_secret_and_views(private_source):
    _, bundle, _, digest = private_source
    assert topology.load_bundle() == bundle
    for view, expected in [('operations', bundle['publicMesh']), ('projection', {}), ('runtime', bundle['publicMesh'])]:
        assert topology.resolve_reference({'kind': 'RestrictedTopologyReference', 'source': topology.SOURCE,
                                           'sourceDigest': digest, 'view': view}) == expected


def test_missing_source_never_reads_history(private_source, monkeypatch):
    monkeypatch.delenv('NETWORK_TOPOLOGY_BUNDLE')
    with pytest.raises(ValueError, match='unavailable'):
        topology.load_bundle()


def test_wrong_digest_and_extra_reference_fields_rejected(private_source, monkeypatch):
    _, _, raw, digest = private_source
    monkeypatch.setenv('NETWORK_TOPOLOGY_BUNDLE', base64.b64encode(gzip.compress(raw + b' ')).decode())
    with pytest.raises(ValueError, match='version mismatch'):
        topology.load_bundle()
    with pytest.raises(ValueError, match='Invalid topology reference'):
        topology.resolve_reference({'kind': 'RestrictedTopologyReference', 'source': topology.SOURCE,
                                    'sourceDigest': digest, 'view': 'operations', 'nodes': []})


def test_compression_bomb_and_checkout_plaintext_rejected(private_source, monkeypatch, tmp_path):
    fake_root, _, raw, _ = private_source
    monkeypatch.setenv('NETWORK_TOPOLOGY_BUNDLE', base64.b64encode(gzip.compress(b'x' * (topology.MAX_BYTES + 1))).decode())
    with pytest.raises(ValueError, match='version mismatch'):
        topology.load_bundle()
    monkeypatch.delenv('NETWORK_TOPOLOGY_BUNDLE')
    path = fake_root / 'private.json'
    path.write_bytes(raw)
    monkeypatch.setenv('NETWORK_TOPOLOGY_FILE', str(path))
    with pytest.raises(ValueError, match='outside the checkout'):
        topology.load_bundle()
    outside = tmp_path / 'private.json'
    outside.write_bytes(raw)
    monkeypatch.setenv('NETWORK_TOPOLOGY_FILE', str(outside))
    assert topology.load_bundle()['schemaVersion'] == 1


def test_public_inventory_and_projection_boundary():
    topology.verify_public()
    docs = [x for x in yaml.safe_load_all((ROOT / 'clusters/re8ch/domains/networking/resources.yaml').read_text()) if x]
    public_mesh = next(x for x in docs if x['kind'] == 'ConfigMap' and x['metadata']['name'] == 'public-mesh-podcidr')
    assert 'sops' in public_mesh
    assert all(x.startswith('ENC[AES256_GCM,') for x in public_mesh['data'].values())
    root = yaml.safe_load((ROOT / 'clusters/re8ch/kustomization.yaml').read_text())
    assert 'secrets/restricted-topology-context.sops.yaml' in root['resources']


def test_runtime_permission_is_exact_get_and_diagnostics_publish_no_artifacts():
    docs = list(yaml.safe_load_all((ROOT / 'clusters/re8ch/components/ops/github-actions-topology-reader-rbac.yaml').read_text()))
    assert docs[0]['metadata']['namespace'] == 'ops'
    assert docs[0]['rules'] == [{'apiGroups': [''], 'resources': ['configmaps'],
                                'resourceNames': ['restricted-topology-context'], 'verbs': ['get']}]
    workflow = yaml.safe_load((ROOT / '.github/workflows/node-contract-observer.yml').read_text())
    job = workflow['jobs']['topology']
    assert job['environment'] == 'cluster-observation'
    assert "github.ref == 'refs/heads/main'" in job['if']
    assert not any(s.get('uses', '').startswith('actions/upload-artifact') for s in job['steps'])
    assert any('runtime-check' in s.get('run', '') for s in job['steps'])


def test_platform_memories_do_not_embed_address_topology():
    for memory in (ROOT / 'catalogs/platform/modules').glob('*/MEMORY.md'):
        text = memory.read_text()
        assert not re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text), str(memory)
        assert 'contracts/network-topology/README.md' in text
    assert 'read_text' not in (ROOT / 'operations/workspace-controller/memory_catalog.py').read_text().split('def public_authority_context')[1].split('def git')[0]


def test_cli_never_prints_invalid_private_input():
    import os
    import subprocess
    marker = 'PRIVATE-CONTEXT-SENTINEL'
    result = subprocess.run([sys.executable, str(ROOT / 'operations/network/restricted_topology.py'), 'validate'],
        env={**os.environ, 'NETWORK_TOPOLOGY_BUNDLE': marker}, capture_output=True, text=True)
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    assert 'Restricted topology check failed' in result.stderr


def test_fabric_renderer_cannot_write_decoded_topology_into_checkout(tmp_path, monkeypatch):
    from fabric import fabric_model
    monkeypatch.setattr(fabric_model, 'ROOT', tmp_path / 'checkout')
    monkeypatch.setattr(fabric_model, 'load_model', lambda _: {})
    monkeypatch.setattr(fabric_model, 'validate', lambda _: {'inventory.json': {'private': 'synthetic'}})
    monkeypatch.setattr(sys, 'argv', ['fabric_model.py', 'render', '--output-root', str(tmp_path / 'checkout' / 'output')])
    with pytest.raises(SystemExit):
        fabric_model.main()
    output = tmp_path / 'private-output'
    monkeypatch.setattr(sys, 'argv', ['fabric_model.py', 'render', '--output-root', str(output)])
    fabric_model.main()
    assert json.loads((output / 'inventory.json').read_text()) == {'private': 'synthetic'}
    assert (output / 'inventory.json').stat().st_mode & 0o777 == 0o600
