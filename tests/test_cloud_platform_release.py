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
    assert jobs['finalize']['needs'] == ['validate', 'release']
    assert jobs['finalize']['permissions']['id-token'] == 'write'
    assert jobs['validate']['runs-on'] == ['self-hosted', 'Linux', 'X64', 'r640', 'untrusted-build']
    assert "inputs.publisher == 'r640'" in release['runs-on']
    assert release['runs-on'].endswith("|| 'ubuntu-24.04' }}")
    dispatch = workflow[True]['workflow_dispatch']['inputs']['publisher']
    assert dispatch['default'] == 'cloud' and dispatch['options'] == ['cloud', 'r640']
    fallback = next(step for step in release['steps'] if step.get('name') == 'Reuse the existing host-proxy builder')
    assert fallback['if'] == "inputs.publisher == 'r640'"
    assert 'docker buildx inspect r640-mihomos' in fallback['run']
    assert 'create' not in fallback['run']
    assert jobs['finalize']['runs-on'] == 'ubuntu-24.04'
    assert jobs['finalize']['permissions']['actions'] == 'read'
    assert "inputs.reuse_run_id == ''" in release['if']
    assert "needs.release.result == 'skipped'" in jobs['finalize']['if']
    source = next(step for step in jobs['finalize']['steps'] if step.get('id') == 'source')
    assert 'git merge-base --is-ancestor' in source['run']
    assert '.conclusion == "success"' in source['run']
    download = next(step for step in jobs['finalize']['steps'] if 'actions/download-artifact@' in step.get('uses', ''))
    assert download['with']['run-id'] == '${{ steps.source.outputs.build_run }}'
    validation = '\n'.join(step.get('run', '') for step in jobs['validate']['steps'])
    assert 'docker build --load -f operations/cloud-platform/Dockerfile' in validation
    assert 'docker run --rm --network none' in validation
    assert 'server.PRIVILEGED_TOOLS & viewer' in validation
    assert 'operations/plugin-capabilities/verify.py' in validation
    publisher = next(step for step in release['steps'] if step.get('run') == './operations/mcp-conformance/release-tenant-infrastructure.sh')
    assert publisher['env']['TENANT_IMAGE_REPOSITORY'] == 'registry.re8ch.com/functions-shared/re8ch-cloud-platform'
    assert publisher['env']['TENANT_IMAGE_DOCKERFILE'] == 'operations/cloud-platform/Dockerfile'
    assert publisher['env']['TENANT_IMAGE_INLINE_AUTH'] == '1'
    assert publisher['env']['TENANT_IMAGE_SKIP_FINALIZE'] == '1'
    assert any('docker/setup-buildx-action@' in step.get('uses', '') for step in release['steps'])
    assert any('cloud-platform-release.yml@refs/heads/main' in step.get('run', '') and 'cosign verify' in step.get('run', '') for step in jobs['finalize']['steps'])


def test_native_upload_attempts_fit_the_release_budget():
    workflow = yaml.safe_load((ROOT / '.github/workflows/cloud-platform-release.yml').read_text())
    release = workflow['jobs']['release']
    publisher = next(step for step in release['steps'] if step.get('run') == './operations/mcp-conformance/release-tenant-infrastructure.sh')
    settings = publisher['env']
    attempts = int(settings['TENANT_IMAGE_PUSH_ATTEMPTS'])
    duration = int(settings['TENANT_IMAGE_PUSH_TIMEOUT_SECONDS'])
    assert 1 <= attempts <= 3 and 60 <= duration <= 3600
    # Includes hard-kill grace and retry delay, with time for signing and SBOM.
    assert release['timeout-minutes'] * 60 - attempts * (duration + 30) - 30 >= 8 * 60


def test_network_planner_builds_on_runner_architecture_for_amd64_release():
    dockerfile = (ROOT / 'operations/cloud-platform/Dockerfile').read_text()
    assert 'FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS network-planner' in dockerfile
    assert 'ARG TARGETOS TARGETARCH' in dockerfile
    assert 'CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build' in dockerfile
