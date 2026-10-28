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
    assert 'org.opencontainers.image.source="https://github.com/re8ch/service-backend"' in dockerfile
    assert "runs-on: [self-hosted, Linux, X64, r640, untrusted-build]" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "environment: production" in workflow
    assert "packages: write" in workflow
    assert "id-token: write" in workflow
    assert "--platform linux/amd64" in workflow
    assert "--cache-to type=gha,mode=max" in workflow
    assert "VERSION: 0.6.2-re8ch.4" in workflow
    assert "IMAGE: ghcr.io/re8ch/toolhive-cloud-ui-re8ch" in workflow
    assert 'cosign sign --yes "$IMAGE@$digest"' in workflow
    assert "workflow_dispatch:" in workflow


def test_directory_pr_validation_builds_the_same_tested_image_without_publishing():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/toolhive-cloud-ui-release.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    assert jobs['release']['needs'] == 'validate'
    assert "github.event_name != 'pull_request'" in jobs['release']['if']
    assert "github.ref == 'refs/heads/main'" in jobs['release']['if']
    assert 'environment' not in jobs['validate']
    steps = jobs['validate']['steps']
    assert any('docker build' in step.get('run', '') for step in steps)
    assert not any('--push' in step.get('run', '') for step in steps)
    dockerfile = (ROOT / 'clusters/re8ch/components/toolhive/cloud-ui-re8ch/Dockerfile').read_text()
    assert 'pnpm exec vitest run && pnpm type-check && pnpm lint && pnpm build' in dockerfile
