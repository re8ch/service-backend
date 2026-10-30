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
    assert '--provenance=false --progress plain --load' in release
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
