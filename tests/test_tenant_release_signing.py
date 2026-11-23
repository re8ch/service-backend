from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_tenant_release_uses_github_oidc_and_pinned_digest_verification():
    workflow = (ROOT / ".github/workflows/tenant-release.yml").read_text()
    release = (ROOT / "operations/mcp-conformance/release-tenant-infrastructure.sh").read_text()
    assert "id-token: write" in workflow
    assert "environment: production" in workflow
    assert "token.actions.githubusercontent.com" in release
    assert "cosign sign --yes \"$immutable\"" in release
    assert "--certificate-identity \"$certificate_identity\"" in release
    assert 'type=sbom,generator=${sbom_generator}' in release
    assert 'docker.1ms.run/docker/buildkit-syft-scanner:stable-1' in release
    assert '--password-stdin' in release
    assert 'HARBOR_PASSWORD' in release
    assert 'docker sbom --platform linux/amd64' in release
    assert 'syft "$immutable" -o spdx-json' in release
    assert 'TENANT_IMAGE_PUSH_ATTEMPTS' in release
    assert 'fresh registry upload session' in release
    assert 'docker push "$image"' in release
    assert "docker buildx imagetools inspect" in release
    assert 'attestation_args=(--provenance=false)' in release
    assert "workflow_dispatch:" in workflow
    assert "clusters/re8ch/components/ai-operations/**" in workflow

def test_gitlab_pipeline_definition_is_retired():
    assert not (ROOT / ".gitlab-ci.yml").exists()


def test_publisher_executes_configured_upload_timeout_without_network(tmp_path):
    import json
    import os
    import subprocess
    import sys

    bindir = tmp_path / 'bin'
    bindir.mkdir()
    def executable(name, body):
        path = bindir / name
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
    executable('docker', '''import json, pathlib, sys
args = sys.argv[1:]
if args[:2] == ['buildx', 'build'] and '--metadata-file' in args:
    pathlib.Path(args[args.index('--metadata-file') + 1]).write_text(json.dumps({'containerimage.digest': 'sha256:' + 'a' * 64}))
elif args[:3] == ['buildx', 'imagetools', 'inspect']:
    print(json.dumps({'digest': 'sha256:' + 'b' * 64}))
''')
    executable('cosign', 'pass\n')
    executable('timeout', '''import json, os, subprocess, sys
with open(os.environ['TIMEOUT_RECORD'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(subprocess.call(sys.argv[4:]))
''')
    env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
           'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': 'test-source',
           'HARBOR_USERNAME': 'test-only', 'HARBOR_PASSWORD': 'test-only',
           'TENANT_IMAGE_INLINE_AUTH': '', 'COSIGN_KEY': '',
           'TIMEOUT_RECORD': str(tmp_path / 'timeouts.jsonl')}
    script = str(ROOT / 'operations/mcp-conformance/release-tenant-infrastructure.sh')
    for setting, duration in [(None, '300s'), ('1800', '1800s'), ('3600', '3600s')]:
        env.pop('TENANT_IMAGE_PUSH_TIMEOUT_SECONDS', None)
        if setting:
            env['TENANT_IMAGE_PUSH_TIMEOUT_SECONDS'] = setting
        (tmp_path / 'timeouts.jsonl').write_text('')
        result = subprocess.run(['bash', script], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        calls = [json.loads(line) for line in (tmp_path / 'timeouts.jsonl').read_text().splitlines()]
        upload = [row for row in calls if row[3:6] == ['docker', 'buildx', 'build']]
        assert len(upload) == 1 and upload[0][:3] == ['--signal=TERM', '--kill-after=30s', duration]
        assert (tmp_path / 'tenant-infrastructure.image').read_text().strip().endswith('@sha256:' + 'a' * 64)
    env['TENANT_IMAGE_DOCKER_PUSH'] = '1'
    env['TENANT_IMAGE_PUSH_TIMEOUT_SECONDS'] = '300'
    (tmp_path / 'timeouts.jsonl').write_text('')
    result = subprocess.run(['bash', script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (tmp_path / 'timeouts.jsonl').read_text().splitlines()]
    assert any(row[3:5] == ['docker', 'push'] for row in calls)
    assert any('--load' in row and '--provenance=false' in row for row in calls)
    assert (tmp_path / 'tenant-infrastructure.image').read_text().strip().endswith('@sha256:' + 'b' * 64)
    env.pop('TENANT_IMAGE_DOCKER_PUSH')
    for invalid in ['0', '59', '3601', '0100', 'unbounded']:
        env['TENANT_IMAGE_PUSH_TIMEOUT_SECONDS'] = invalid
        result = subprocess.run(['bash', script], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert 'must be an integer from 60 through 3600' in result.stderr


def test_attested_engine_publisher_preserves_full_index_or_fails_closed(tmp_path):
    """Run the actual publisher with a recording Docker CLI, not a string mirror."""
    import hashlib
    import json
    import os
    import subprocess
    import sys

    bindir = tmp_path / 'bin'
    bindir.mkdir()
    docker = bindir / 'docker'
    docker.write_text(f'#!{sys.executable}\n' + '''import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_RECORD'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
case = os.environ['PUBLICATION_CASE']
subject = 'sha256:' + 'b' * 64
index = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [
    {'digest': subject, 'platform': {'os': 'linux', 'architecture': 'amd64'}},
    {'digest': 'sha256:' + 'c' * 64, 'annotations': {'vnd.docker.reference.type': 'attestation-manifest',
        'vnd.docker.reference.digest': 'sha256:' + ('d' if case == 'wrong-subject' else 'b') * 64}}]}
raw = json.dumps(index, separators=(',', ':'))
digest = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
if args[:1] == ['info']:
    print(json.dumps([] if case == 'classic' else [['driver-type', 'io.containerd.snapshotter.v1']]))
elif args[:3] == ['buildx', 'imagetools', 'inspect']:
    if '--raw' in args:
        print(raw + (' ' if case == 'wrong-bytes' else ''))
    elif '.Provenance' in args[-1]:
        value = {} if case == 'missing-provenance' else {'linux/amd64': {'SLSA': {
            'buildType': 'https://mobyproject.org/buildkit@v1', 'metadata': {
                'https://mobyproject.org/buildkit@v1#metadata': {
                    'vcs': {'revision': ('e' * 40 if case == 'wrong-source' else os.environ['GITHUB_SHA'])},
                    'source': {'infos': [{'filename': 'Dockerfile'}]}}}}}}
        if case == 'valid-v1':
            old = value['linux/amd64']['SLSA']
            value['linux/amd64']['SLSA'] = {'buildDefinition': {'buildType': old['buildType']},
                'runDetails': {'metadata': {'buildkit_metadata': old['metadata']['https://mobyproject.org/buildkit@v1#metadata']}}}
        print(json.dumps(value))
    elif '.SBOM' in args[-1]:
        print(json.dumps({} if case == 'missing-sbom' else {'linux/amd64': {'SPDX': {'spdxVersion': 'SPDX-2.3', 'packages': [{'name': 'fixture'}]}}}))
    else:
        print(json.dumps({'digest': digest}))
''')
    docker.chmod(0o755)
    script = str(ROOT / 'operations/mcp-conformance/release-tenant-infrastructure.sh')
    for case in ('valid', 'valid-v1', 'classic', 'missing-provenance', 'missing-sbom', 'wrong-source', 'wrong-subject', 'wrong-bytes'):
        folder = tmp_path / case
        folder.mkdir()
        record = folder / 'calls.jsonl'
        env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
               'GITHUB_ACTIONS': '', 'GITHUB_SHA': 'a' * 40,
               'TENANT_IMAGE_SKIP_FINALIZE': '1', 'TENANT_IMAGE_DOCKER_PUSH': '1',
               'TENANT_IMAGE_REQUIRE_ATTESTATIONS': '1', 'TENANT_IMAGE_PUSH_ATTEMPTS': '1',
               'TENANT_IMAGE_INLINE_AUTH': '', 'HARBOR_USERNAME': 'test-only', 'HARBOR_PASSWORD': 'test-only',
               'DOCKER_RECORD': str(record), 'PUBLICATION_CASE': case}
        result = subprocess.run(['bash', script], cwd=folder, env=env, text=True, capture_output=True)
        calls = [json.loads(row) for row in record.read_text().splitlines()]
        builds = [row for row in calls if row[:2] == ['buildx', 'build']]
        if case == 'classic':
            assert not builds
            assert 'requires the containerd image store' in result.stderr
        else:
            assert len(builds) == 1
            assert '--load' in builds[0] and '--provenance=mode=max' in builds[0]
            assert any(v.startswith('type=sbom,generator=') for v in builds[0])
            assert '--push' not in builds[0] and '--provenance=false' not in builds[0]
            pushes = [row for row in calls if row[:1] == ['push']]
            assert len(pushes) == 1 and '--platform' not in pushes[0]
        image = folder / 'tenant-infrastructure.image'
        assert (result.returncode == 0) == (case in {'valid', 'valid-v1'}), result.stderr
        assert image.exists() == (case in {'valid', 'valid-v1'})
        if case in {'valid', 'valid-v1'}:
            raw = (folder / 'tenant-infrastructure.index.json').read_bytes().removesuffix(b'\n')
            assert image.read_text().strip().endswith('@sha256:' + hashlib.sha256(raw).hexdigest())
