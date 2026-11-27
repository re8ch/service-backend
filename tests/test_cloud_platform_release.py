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
    assert jobs['validate']['runs-on'] == 'ubuntu-24.04'
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
    assert validation.count('./operations/mcp-conformance/release-tenant-infrastructure.sh') == 1
    assert '127.0.0.1:5000:5000 registry@sha256:' in validation
    ci_publisher = next(step for step in jobs['validate']['steps'] if 'verify native attested Engine push' in step.get('name', ''))
    assert ci_publisher['env']['TENANT_IMAGE_DOCKERFILE'] == 'operations/cloud-platform/Dockerfile'
    assert ci_publisher['env']['TENANT_IMAGE_REQUIRE_ATTESTATIONS'] == '1'
    assert ci_publisher['env']['TENANT_IMAGE_DOCKER_PUSH'] == '1'
    assert not any('secrets.' in str(step) for step in jobs['validate']['steps'])
    assert any(step.get('if') == 'always()' and 'docker rm -f cloud-ci-registry' in step.get('run', '') for step in jobs['validate']['steps'])
    assert 'docker run --rm --network none' in validation
    assert 'server.PRIVILEGED_TOOLS & viewer' in validation
    assert 'operations/plugin-capabilities/verify.py' in validation
    publisher = next(step for step in release['steps'] if step.get('run') == './operations/mcp-conformance/release-tenant-infrastructure.sh')
    assert publisher['env']['TENANT_IMAGE_REPOSITORY'] == 'ghcr.io/re8ch/service-backend-cloud-platform'
    assert publisher['env']['TENANT_IMAGE_DOCKERFILE'] == 'operations/cloud-platform/Dockerfile'
    assert publisher['env']['TENANT_IMAGE_INLINE_AUTH'] == '1'
    assert publisher['env']['TENANT_IMAGE_SKIP_FINALIZE'] == '1'
    docker = next(step for step in release['steps'] if 'docker/setup-docker-action@' in step.get('uses', ''))
    assert docker['if'] == "inputs.publisher != 'r640'"
    assert docker['with']['set-host'] is True
    assert 'containerd-snapshotter' in docker['with']['daemon-config']
    buildx = next(step for step in release['steps'] if 'docker/setup-buildx-action@' in step.get('uses', ''))
    assert buildx['with']['driver'] == 'docker'
    assert publisher['env']['TENANT_IMAGE_DOCKER_PUSH'] == '1'
    assert publisher['env']['TENANT_IMAGE_REQUIRE_ATTESTATIONS'] == '1'
    uploaded = next(step for step in release['steps'] if 'actions/upload-artifact@' in step.get('uses', ''))
    for evidence in ('index.json', 'provenance.json', 'sbom.json'):
        assert 'cloud-platform.' + evidence in uploaded['with']['path']
    assert any('cloud-platform-release.yml@refs/heads/main' in step.get('run', '') and 'cosign verify' in step.get('run', '') for step in jobs['finalize']['steps'])
    assert release['permissions']['packages'] == 'write'
    assert jobs['finalize']['permissions']['packages'] == 'write'
    primary = next(step for step in jobs['finalize']['steps'] if step.get('id') == 'primary')
    assert '.visibility == "public"' in primary['run']
    assert '.private == false' in primary['run']
    assert 'cloud-image-transfer.sh prove' in primary['run']
    mirror = next(step for step in jobs['finalize']['steps'] if step.get('id') == 'mirror')
    assert mirror['continue-on-error'] is True
    assert mirror['env']['CERTIFIED_SOURCE_SHA'] == '${{ steps.source.outputs.sha }}'
    assert 'cloud-image-transfer.sh mirror' in mirror['run']
    assert jobs['finalize']['steps'].index(primary) < jobs['finalize']['steps'].index(mirror)
    assert 'org.opencontainers.image.source' in (ROOT / 'operations/cloud-platform/Dockerfile').read_text()


def test_native_upload_attempts_fit_the_release_budget():
    workflow = yaml.safe_load((ROOT / '.github/workflows/cloud-platform-release.yml').read_text())
    release = workflow['jobs']['release']
    publisher = next(step for step in release['steps'] if step.get('run') == './operations/mcp-conformance/release-tenant-infrastructure.sh')
    settings = publisher['env']
    attempts = int(settings['TENANT_IMAGE_PUSH_ATTEMPTS'])
    duration = int(settings['TENANT_IMAGE_PUSH_TIMEOUT_SECONDS'])
    assert 1 <= attempts <= 3 and 60 <= duration <= 3600
    assert attempts == 1 and duration == 1800
    # Includes hard-kill grace and retry delay, with time for signing and SBOM.
    assert release['timeout-minutes'] * 60 - attempts * (duration + 30) - 30 >= 8 * 60


def test_network_planner_builds_on_runner_architecture_for_amd64_release():
    dockerfile = (ROOT / 'operations/cloud-platform/Dockerfile').read_text()
    assert 'FROM --platform=$BUILDPLATFORM registry.re8ch.com/proxy-dockerhub/library/golang:1.26.10-alpine AS network-planner' in dockerfile
    assert 'ARG TARGETOS TARGETARCH' in dockerfile
    assert 'CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build' in dockerfile
