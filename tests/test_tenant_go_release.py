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


def test_tenant_go_release_uses_stable_router_and_local_go_cache():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tenant-go-release.yml").read_text()
    )
    validation = workflow["jobs"]["tenant-go-validate"]
    setup_go = next(
        step for step in validation["steps"] if step.get("uses", "").startswith("actions/setup-go@")
    )

    assert validation["name"] == "tenant-go-validate"
    assert setup_go["with"]["cache"] is False
    assert workflow["jobs"]["release"]["needs"] == "tenant-go-validate"


def test_tenant_go_release_builds_on_r640_without_mac_network_mutation():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tenant-go-release.yml").read_text()
    )
    release = workflow["jobs"]["release"]
    prepare = next(
        step["run"]
        for step in release["steps"]
        if step.get("name") == "Prepare isolated restricted-node-6dc3f140f9 release authentication"
    )

    assert release["runs-on"] == ["self-hosted", "Linux", "X64", "restricted-node-6dc3f140f9", "untrusted-build"]
    assert "r640-mihomos" in prepare
    assert "macmini" not in prepare.lower()
    assert "docker exec" not in prepare
    assert "/etc/resolv.conf" not in prepare


def test_split_publisher_does_not_require_finalize_only_tools():
    publisher = (
        ROOT / "operations/mcp-conformance/release-tenant-infrastructure.sh"
    ).read_text()

    assert 'if [[ "$TENANT_IMAGE_SKIP_FINALIZE" != "1" ]]' in publisher
    assert "command -v cosign" in publisher
    assert "docker info" not in publisher
