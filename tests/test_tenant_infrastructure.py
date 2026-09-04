import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "clusters/re8ch/components/ai-operations/tenant_infrastructure.py"
spec = importlib.util.spec_from_file_location("tenant_infrastructure", MODULE)
tenant_infrastructure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tenant_infrastructure)


def database_spec(**parameters):
    return {"serviceClass": "database.postgresql.shared.v1", "planRef": "standard", "regionRef": "loudi", "desiredState": "Ready", "parameters": {"storageGiB": 20, **parameters}}


def volume_spec(size=10):
    return {"serviceClass": "storage.volume.v1", "planRef": "rbd", "regionRef": "loudi", "desiredState": "Ready", "parameters": {"sizeGiB": size, "accessMode": "ReadWriteOnce"}}


def test_catalog_has_service_specific_consumables():
    names = {item["serviceClass"] for item in tenant_infrastructure.catalog()}
    assert {"database.postgresql.shared.v1", "storage.volume.v1", "storage.bucket.v1", "network.tenant.v1", "network.among-clusters.v1", "registry.harbor.v1", "observability.grafana.v1", "ai.model-gateway.v1", "sandbox.runtime.v1"} == names
    network = next(item for item in tenant_infrastructure.catalog() if item["serviceClass"] == "network.tenant.v1")
    assert {"public", "full"} <= set(network["plans"])
    grafana = next(item for item in tenant_infrastructure.catalog() if item["serviceClass"] == "observability.grafana.v1")
    assert grafana["accessUnit"] == "OrganizationFolderDatasourceGrant"


def test_create_is_tenant_scoped_idempotent_and_secret_free(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    first = store.mutate("qwen", "primary", database_spec(), "create-0001")
    replay = store.mutate("qwen", "primary", database_spec(), "create-0001")
    assert replay["operationId"] == first["operationId"]
    assert replay["idempotentReplay"] is True
    assert first["credentialMaterialReturned"] is False
    with pytest.raises(tenant_infrastructure.ServiceError, match="not found"):
        store.get("ledger", first["resourceRef"])


def test_idempotency_key_cannot_be_reused_for_different_request(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    store.mutate("qwen", "primary", database_spec(), "create-0001")
    with pytest.raises(tenant_infrastructure.ServiceError, match="different request"):
        store.mutate("qwen", "primary", database_spec(storageGiB=40), "create-0001")


def test_volume_can_expand_but_not_shrink(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    store.mutate("qwen", "data", volume_spec(10), "volume-001")
    store.mutate("qwen", "data", volume_spec(20), "volume-002", "update")
    with pytest.raises(tenant_infrastructure.ServiceError, match="shrink"):
        store.mutate("qwen", "data", volume_spec(5), "volume-003", "update")


def test_soft_delete_and_binding_revocation(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    created = store.mutate("qwen", "primary", database_spec(), "create-0001")
    binding = store.create_binding("qwen", created["resourceRef"], "runtime", {})
    assert binding["accessRef"].startswith("access://tenant/qwen/")
    revoked = store.revoke_binding("qwen", binding["bindingRef"])
    assert revoked["credentialRevoked"] is True
    deleted = database_spec(); deleted["desiredState"] = "Deleted"
    result = store.mutate("qwen", "primary", deleted, "delete-0001", "delete")
    assert result["state"] == "Deleting"
    assert store.get("qwen", created["resourceRef"])["deleted_at"]


def test_admin_manages_generic_tenants_grants_visibility_and_deletion(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    tenant = store.create_tenant("research-a", "Research A", "oidc-group:research-a")
    assert tenant["tenantRef"] == "research-a"
    grant = store.create_grant("research-a", "observability.grafana.v1", "viewer", "Tenant", {"folderRefs": ["team-a"], "datasourceRefs": ["metrics-a"]})
    assert grant["accessRef"].startswith("access://tenant/research-a/")
    assert grant["credentialMaterialReturned"] is False
    assert store.set_visibility("research-a", "Shared")["visibility"] == "Shared"
    assert store.delete_tenant("research-a")["grantsRevoked"] is True
    with pytest.raises(tenant_infrastructure.ServiceError, match="admin"):
        store.create_tenant("admin", "Admin", "oidc:admin")


def test_usage_outbox_is_deduplicated_and_unpriced(tmp_path):
    store = tenant_infrastructure.ServiceStore(tmp_path / "services.db")
    created = store.mutate("qwen", "primary", database_spec(), "create-0001")
    args = ("qwen", created["resourceRef"], "instance_hours", 1.0, "hour", "2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z", created["operationId"])
    first = store.record_usage(*args)
    second = store.record_usage(*args)
    assert first["eventId"] == second["eventId"]
    summary = store.usage_summary("qwen")
    assert summary["eventCount"] == 1
    assert summary["totals"]["instance_hours:hour"] == 1
    assert summary["pricingApplied"] is False


def test_credentials_and_public_bucket_are_rejected():
    value = database_spec(apiToken="bad")
    with pytest.raises(tenant_infrastructure.ServiceError, match="credential material"):
        tenant_infrastructure.validate_spec(value)
    bucket = {"serviceClass": "storage.bucket.v1", "planRef": "dedicated", "regionRef": "guangzhou", "desiredState": "Ready", "parameters": {"quotaGiB": 10, "visibility": "public"}}
    with pytest.raises(tenant_infrastructure.ServiceError, match="publication binding"):
        tenant_infrastructure.validate_spec(bucket)
