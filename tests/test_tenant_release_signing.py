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
    assert "workflow_dispatch:" in workflow
    assert "clusters/re8ch/components/ai-operations/**" in workflow

def test_gitlab_pipeline_definition_is_retired():
    assert not (ROOT / ".gitlab-ci.yml").exists()
