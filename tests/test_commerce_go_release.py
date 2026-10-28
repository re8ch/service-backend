from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_commerce_release_uses_rotated_domain_registry_identity():
    workflow = (ROOT / ".github/workflows/commerce-go-release.yml").read_text()
    assert "secrets.DOMAIN_REGISTRY_USERNAME" in workflow
    assert "secrets.DOMAIN_REGISTRY_PASSWORD" in workflow
    assert "secrets.HARBOR_USERNAME" not in workflow
    assert "secrets.HARBOR_PASSWORD" not in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert 'DOCKER_CONFIG="${RUNNER_TEMP}/commerce-go-release-docker"' in workflow
    assert 'BUILDX_CONFIG=${HOME}/.docker/buildx' not in workflow
    assert "docker/setup-buildx-action" not in workflow
    assert "docker buildx version" in workflow
    assert 'printf \'{}\\n\' > "$DOCKER_CONFIG/config.json"' in workflow
    assert 'printf \'{"auths":{"registry.re8ch.com":{"auth":"%s"}}}\\n\'' in workflow
    assert 'rm -f "$DOCKER_CONFIG/config.json"' in workflow
