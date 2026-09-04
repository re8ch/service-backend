from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_tenant_release_supports_gitlab_keyless_and_pinned_digest_verification():
    pipeline = (ROOT / ".gitlab-ci.yml").read_text()
    release = (ROOT / "operations/mcp-conformance/release-tenant-infrastructure.sh").read_text()
    assert "SIGSTORE_ID_TOKEN:" in pipeline
    assert "aud: sigstore" in pipeline
    assert 'cosign sign --yes --identity-token "$SIGSTORE_ID_TOKEN" "$immutable"' in release
    assert '--certificate-identity "${CI_PROJECT_URL}//.gitlab-ci.yml@refs/heads/${CI_COMMIT_REF_NAME}"' in release
    assert '--certificate-oidc-issuer "$CI_SERVER_URL"' in release
    assert '"$immutable"' in release
    assert 'type=sbom,generator=${sbom_generator}' in release
    assert 'docker.1ms.run/docker/buildkit-syft-scanner:stable-1' in release
    assert '--password-stdin' in release
    assert 'HARBOR_PASSWORD' in release
