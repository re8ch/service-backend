import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("tenant_contracts", ROOT / "operations/tenant-broker/contracts.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


def compute_claim(kind="Deployment", **parameters):
    values = {"kind": kind, "imageDigest": "registry.re8ch.com/acme/agent@sha256:" + "a" * 64, "resources": {"cpu": "500m", "memory": "1Gi", "ephemeralStorage": "2Gi"}}
    values.update(parameters)
    return {"apiVersion": "tenancy.re8ch.com/v1alpha1", "kind": "TenantResourceClaim", "metadata": {"name": "build-1"}, "spec": {"tenantRef": "acme", "idempotencyKey": "build-0001", "capability": "compute.workload.v1", "ttlSeconds": 3600, "parameters": values}}


def volume_claim(size=20):
    return {"apiVersion": "tenancy.re8ch.com/v1alpha1", "kind": "TenantResourceClaim", "metadata": {"name": "data-1"}, "spec": {"tenantRef": "acme", "idempotencyKey": "volume-0001", "capability": "storage.volume.v1", "ttlSeconds": 86400, "parameters": {"sizeGiB": size, "plan": "rbd"}}}


def test_catalog_only_publishes_compute_and_independent_volume():
    assert {item["name"] for item in contracts.capabilities()} == {"compute.workload.v1", "storage.volume.v1"}
    assert all(item["requiresApproval"] is False for item in contracts.capabilities())


def test_plan_normalizes_defaults_and_reports_showback_impact():
    result = contracts.plan(compute_claim(), authenticated_tenant="acme")
    params = result["normalizedClaim"]["spec"]["parameters"]
    assert params["replicas"] == 1
    assert params["egressClass"] == "metered-public"
    assert result["quotaImpact"]["pods"] == 1
    assert result["requiresApproval"] is False
    assert result["claimHash"].startswith("sha256:")


def test_cross_tenant_and_non_tenant_harbor_image_are_denied():
    with pytest.raises(contracts.ContractError, match="authenticated tenant"):
        contracts.plan(compute_claim(), authenticated_tenant="artc")
    value = compute_claim(imageDigest="registry.re8ch.com/other/agent@sha256:" + "b" * 64)
    with pytest.raises(contracts.ContractError, match="tenant Harbor project"):
        contracts.plan(value, authenticated_tenant="acme")
    mapped = compute_claim(imageDigest="registry.re8ch.com/acme-registry/agent@sha256:" + "c" * 64)
    assert contracts.plan(mapped, authenticated_tenant="acme", harbor_project="acme-registry")["tenantRef"] == "acme"


@pytest.mark.parametrize("key", ["apiToken", "gpu", "hostPath", "serviceAccount"])
def test_privileged_or_credential_parameters_are_denied(key):
    with pytest.raises(contracts.ContractError, match="forbidden"):
        contracts.plan(compute_claim(**{key: "bad"}), authenticated_tenant="acme")


def test_public_anonymous_endpoint_is_deployment_only():
    result = contracts.plan(compute_claim(exposure={"enabled": True, "port": 8080}), authenticated_tenant="acme")
    assert result["quotaImpact"]["publicEndpoints"] == 1
    with pytest.raises(contracts.ContractError, match="Deployment"):
        contracts.plan(compute_claim("Job", exposure={"enabled": True, "port": 8080}), authenticated_tenant="acme")


def test_job_is_immutable_but_deployment_can_scale_and_roll():
    changed = compute_claim("Job", imageDigest="registry.re8ch.com/acme/agent@sha256:" + "b" * 64)
    with pytest.raises(contracts.ContractError, match="Job parameters are immutable"):
        contracts.validate_update(compute_claim("Job"), changed, authenticated_tenant="acme")
    scaled = compute_claim(replicas=3)
    assert contracts.validate_update(compute_claim(), scaled, authenticated_tenant="acme")["spec"]["parameters"]["replicas"] == 3


def test_independent_volume_is_retained_and_cannot_shrink():
    result = contracts.plan(volume_claim(), authenticated_tenant="acme")
    assert result["normalizedClaim"]["spec"]["parameters"]["retentionPolicy"] == "Retain"
    assert result["normalizedClaim"]["spec"]["parameters"]["storageClass"] == "rbd-r640"
    with pytest.raises(contracts.ContractError, match="cannot be reduced"):
        contracts.validate_update(volume_claim(20), volume_claim(10), authenticated_tenant="acme")
    changed_plan = volume_claim(30)
    changed_plan["spec"]["parameters"]["plan"] = "cephfs"
    with pytest.raises(contracts.ContractError, match="immutable"):
        contracts.validate_update(volume_claim(20), changed_plan, authenticated_tenant="acme")


def test_ttl_is_required_and_bounded():
    value = compute_claim()
    value["spec"]["ttlSeconds"] = 60
    with pytest.raises(contracts.ContractError, match="ttlSeconds"):
        contracts.plan(value, authenticated_tenant="acme")
