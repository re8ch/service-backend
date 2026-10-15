from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_tenant_go_release_allows_cold_builder_pulls_within_job_budget():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tenant-go-release.yml").read_text()
    )
    release = workflow["jobs"]["release"]
    publisher = next(
        step
        for step in release["steps"]
        if step.get("run")
        == "./operations/mcp-conformance/release-tenant-infrastructure.sh"
    )

    duration = int(publisher["env"]["TENANT_IMAGE_PUSH_TIMEOUT_SECONDS"])
    assert 300 < duration <= 3600
    assert duration < release["timeout-minutes"] * 60
