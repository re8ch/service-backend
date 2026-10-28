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
    assert "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f" in workflow
    assert "docker buildx version" in workflow
    assert 'printf \'{}\\n\' > "$DOCKER_CONFIG/config.json"' in workflow
    assert "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9" in workflow
    assert 'rm -f "$DOCKER_CONFIG/config.json"' in workflow
