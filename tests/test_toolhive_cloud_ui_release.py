from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_cloud_ui_release_is_remote_pinned_and_signed():
    dockerfile = (
        ROOT
        / "clusters/re8ch/components/toolhive/cloud-ui-re8ch/Dockerfile"
    ).read_text()
    workflow = (
        ROOT / ".github/workflows/toolhive-cloud-ui-release.yml"
    ).read_text()

    assert "TOOLHIVE_CLOUD_UI_REF=d8a92acc24ae48e08579c580108f376708730300" in dockerfile
    assert 'git fetch --depth 1 origin "${TOOLHIVE_CLOUD_UI_REF}"' in dockerfile
    assert "runs-on: ubuntu-latest" in workflow
    assert "environment: production" in workflow
    assert "packages: write" in workflow
    assert "id-token: write" in workflow
    assert "--platform linux/amd64" in workflow
    assert "--cache-to type=gha,mode=max" in workflow
    assert "VERSION: 0.6.2-re8ch.4" in workflow
    assert 'cosign sign --yes "$IMAGE@$digest"' in workflow
    assert "workflow_dispatch:" in workflow
