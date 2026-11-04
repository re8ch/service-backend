from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_only_tenant_publisher_receives_ghcr_write_identity():
    workflow = yaml.safe_load((ROOT / ".github/workflows/tenant-go-release.yml").read_text())
    release = workflow["jobs"]["release"]
    finalize = workflow["jobs"]["finalize"]
    assert release["environment"] == finalize["environment"] == "production"

    publisher = next(s for s in release["steps"] if "IMAGE_REGISTRY_PASSWORD" in s.get("env", {}))
    assert release["permissions"]["packages"] == "write"
    assert publisher["env"]["IMAGE_REGISTRY_USERNAME"] == "${{ github.actor }}"
    assert publisher["env"]["IMAGE_REGISTRY_PASSWORD"] == "${{ secrets.GITHUB_TOKEN }}"
    assert publisher["env"]["TENANT_IMAGE_REPOSITORY"] == "ghcr.io/re8ch/service-backend-tenant-mcp"
    assert "HARBOR_USERNAME" not in publisher["env"]
    assert "HARBOR_PASSWORD" not in publisher["env"]

    # Finalization attests the immutable digest without pushing to GHCR, so it
    # must not receive the registry credentials used only by the build publisher.
    assert not any(
        step.get("uses", "").startswith("docker/login-action@")
        for step in finalize["steps"]
    )
    assert finalize["permissions"].get("packages") != "write"
    attest = next(step for step in finalize["steps"] if step.get("id") == "attest")
    assert attest["with"]["push-to-registry"] is False
    subject = next(s for s in finalize["steps"] if s.get("id") == "subject")
    assert "ghcr\\.io/re8ch/service-backend-tenant-mcp@sha256" in subject["run"]
    anonymous = next(s for s in finalize["steps"] if s.get("name") == "Verify anonymous cluster pull access")
    assert "docker manifest inspect" in anonymous["run"]
    assert "${{ steps.subject.outputs.image }}" in anonymous["run"]


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
    assert publisher["env"]["GODEBUG"] == "http2client=0"


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


def test_tenant_go_release_defaults_to_hosted_ghcr_push_with_r640_fallback():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tenant-go-release.yml").read_text()
    )
    release = workflow["jobs"]["release"]
    prepare = next(
        step["run"]
        for step in release["steps"]
        if step.get("name") == "Prepare isolated cloud release authentication"
    )

    assert "r640" in str(release["runs-on"])
    assert workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]["publisher"]["default"] == "cloud"
    assert ".github/workflows/tenant-go-release.yml" in workflow.get("on", workflow.get(True))["push"]["paths"]
    assert workflow["jobs"]["finalize"]["runs-on"] == "ubuntu-24.04"
    builder = next(s for s in release["steps"] if s.get("id") == "builder")
    assert builder["uses"].startswith("docker/setup-buildx-action@")
    assert builder["if"] == "inputs.publisher != 'r640'"
    assert builder["with"]["driver-opts"] == "env.GODEBUG=http2client=0"
    fallback = next(s for s in release["steps"] if s.get("name") == "Reuse the governed R640 host-proxy builder")
    assert fallback["if"] == "inputs.publisher == 'r640'"
    assert "docker buildx inspect r640-mihomos" in fallback["run"]
    publisher = next(s for s in release["steps"] if "IMAGE_REGISTRY_PASSWORD" in s.get("env", {}))
    assert "TENANT_IMAGE_BUILDER" not in publisher["env"]
    assert publisher["env"]["TENANT_IMAGE_DOCKER_PUSH"] == "0"
    assert "BUILDX_CONFIG" not in prepare
    assert 'DOCKER_CONFIG="${RUNNER_TEMP}/tenant-go-release-docker"' in prepare
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
