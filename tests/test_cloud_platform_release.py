from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]


def test_native_runtime_release_cannot_publish_unvalidated_pr_or_branch():
    workflow = yaml.safe_load((ROOT / '.github/workflows/cloud-platform-release.yml').read_text())
    jobs = workflow['jobs']
    release = jobs['release']
    assert release['needs'] == 'validate'
    assert "github.event_name != 'pull_request'" in release['if']
    assert "github.ref == 'refs/heads/main'" in release['if']
    assert release['environment'] == 'production'
    assert release['permissions']['id-token'] == 'write'
    assert jobs['validate']['runs-on'] == release['runs-on'] == 'ubuntu-latest'
    validation = '\n'.join(step.get('run', '') for step in jobs['validate']['steps'])
    assert 'docker build -f operations/cloud-platform/Dockerfile' in validation
    assert 'docker run --rm --network none' in validation
    assert 'server.PRIVILEGED_TOOLS & viewer' in validation
    assert 'operations/plugin-capabilities/verify.py' in validation
    publisher = next(step for step in release['steps'] if step.get('run') == './operations/mcp-conformance/release-tenant-infrastructure.sh')
    assert publisher['env']['TENANT_IMAGE_REPOSITORY'] == 'registry.re8ch.com/functions-shared/re8ch-cloud-platform'
    assert publisher['env']['TENANT_IMAGE_DOCKERFILE'] == 'operations/cloud-platform/Dockerfile'
    assert 'cloud-platform-release.yml@' in publisher['env']['SIGSTORE_CERTIFICATE_IDENTITY']
