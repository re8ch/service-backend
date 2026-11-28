from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_dynamic_catalog_keeps_one_gitops_owned_toolhive_projection():
    objects = list(yaml.safe_load_all((ROOT / "platform/connectool/release.yaml").read_text()))
    release = next(item for item in objects if item and item.get("kind") == "HelmRelease")
    publication = next(
        item for item in release["spec"]["values"]["toolhive"]["publications"]
        if item["name"] == "re8ch-native"
    )
    assert publication["workloads"] == [{"name": "tenant-api-backend"}]
    assert publication["operational"]["listChanged"]["disabledWorkloads"] == []


def test_postgresql_catalog_schema_has_transactional_projection_primitives():
    schema = (ROOT / "platform/apps/tenant-mcp/catalog-postgresql.sql").read_text()
    for table in ("mcp_catalog_revisions", "mcp_catalog_heads", "mcp_catalog_outbox"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
    assert "GENERATED ALWAYS AS IDENTITY" in schema
    assert "UNIQUE (tenant_ref, operation_key)" in schema
    assert "event_id text NOT NULL UNIQUE" in schema


def test_authority_contract_forbids_per_tool_crds():
    contract = (ROOT / "operations/toolhive-catalog/DYNAMIC-PROJECTION.md").read_text()
    assert "do not create Kubernetes" in contract
    assert "must never apply a per-tool ToolHive CRD" in contract
    assert "bounded Cloud SSA executor" in contract
