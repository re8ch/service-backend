import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("tenant_contracts", ROOT / "operations/tenant-broker/contracts.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


def claim(capability="image.run.v1", parameters=None):
    return {"apiVersion": "tenancy.re8ch.com/v1alpha1", "kind": "TenantResourceClaim", "metadata": {"name": "build-1", "tenantRef": "qwen", "idempotencyKey": "build-0001"}, "spec": {"capability": capability, "ttlSeconds": 3600, "parameters": parameters or {"imageDigest": "registry/image@sha256:" + "a" * 64, "cpu": "2", "memory": "4Gi"}, "consumer": {"cluster": "qwen", "publicEdgeRef": "ynzs-public"}}}


def test_capability_catalog_has_compute_network_storage_and_database():
    names = {item["name"] for item in contracts.capabilities()}
    assert {"image.run.v1", "network.service-binding.v1", "storage.volume.v1", "database.run.v1"} <= names


def test_plan_is_tenant_scoped_and_returns_no_credentials():
    result = contracts.plan(claim(), authenticated_tenant="qwen")
    assert result["tenantRef"] == "qwen"
    assert result["credentialMaterialReturned"] is False
    assert result["claimHash"].startswith("sha256:")


def test_cross_tenant_claim_is_denied():
    with pytest.raises(contracts.ContractError, match="authenticated tenant"):
        contracts.plan(claim(), authenticated_tenant="artc")


def test_inline_credentials_are_denied():
    with pytest.raises(contracts.ContractError, match="credential material"):
        contracts.plan(claim(parameters={"apiToken": "bad"}), authenticated_tenant="qwen")


def test_public_edge_binding_requires_approval():
    value = claim("network.service-binding.v1", {"serviceRef": "job-output", "ports": [443], "protocol": "HTTPS"})
    assert contracts.plan(value, authenticated_tenant="qwen")["requiresApproval"] is True


def test_harbor_claim_is_tenant_scoped_and_destructive_operations_require_approval():
    value = claim("harbor.registry.v1", {"project": "qianwen", "operation": "delete-artifact", "repository": "api", "reference": "sha256:" + "a" * 64})
    result = contracts.plan(value, authenticated_tenant="qwen")
    assert result["bindingType"] == "credentialRef"
    assert result["requiresApproval"] is True
    assert result["credentialMaterialReturned"] is False
