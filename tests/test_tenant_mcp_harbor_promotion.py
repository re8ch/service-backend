from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tenant_mcp_promotion_reuses_one_verified_artifact():
    workflow = (ROOT / ".github/workflows/tenant-mcp-harbor-promote.yml").read_text()

    assert "clusters/re8ch/tenant-mcp-image/image.yaml" in workflow
    assert 'test "$SOURCE_DIGEST" = "$desired"' in workflow
    assert "docker buildx imagetools create" in workflow
    assert 'select(.platform.os == "linux" and .platform.architecture == "amd64")' in workflow
    assert "expected exactly one linux/amd64 child manifest" in workflow
    assert "config:.config.digest,layers:[.layers[].digest]" in workflow
    assert "diff -u source-content.json target-content.json" in workflow
    assert "docker build " not in workflow
    assert "docker buildx build" not in workflow
    assert "environment: production" in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "TENANT_MCP_REGISTRY_USERNAME" in workflow
    assert "TENANT_MCP_REGISTRY_PASSWORD" in workflow
    assert 'BUILDX_CONFIG="${HOME}/.docker/buildx"' in workflow
    assert "docker buildx inspect r640-mihomos" in workflow
