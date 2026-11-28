from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/supabase-billing/tenant-runtime-state-release/tenant-runtime-state.sql"


@pytest.fixture
def runtime_database(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="tenant-runtime-pg-", dir="/tmp"))
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "--no-instructions"],
                   check=True, capture_output=True, text=True)
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(tmp_path / "postgres.log"),
                    "-o", f"-k {socket} -c listen_addresses=''", "-w", "start"],
                   check=True, capture_output=True, text=True)

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket),
                                 "-d", "postgres", "-Atqc", statement],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        sql("""
          create role re8ch_billing_owner;
          create role re8ch_billing_migrator;
          create role billing_commerce_runtime;
          grant re8ch_billing_owner to re8ch_billing_migrator;
          grant billing_commerce_runtime to re8ch_billing_migrator with admin option;
          alter database postgres owner to re8ch_billing_owner;
        """)
        sql(MIGRATION.read_text())
        yield sql
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"],
                       check=True, capture_output=True, text=True)
        socket.rmdir()


def test_runtime_schema_covers_existing_state_stores(runtime_database):
    sql = runtime_database
    tables = sql("""
      select table_name from information_schema.tables
      where table_schema='tenant_runtime' order by table_name
    """).splitlines()
    assert set(tables) == {
        "admin_database_operations", "bindings", "cluster_connections",
        "cluster_registrations", "collaboration_connections", "collaboration_writes",
        "credential_adoptions", "credential_redemptions", "dynamic_skills", "instances",
        "mcp_catalog_heads", "mcp_catalog_outbox", "mcp_catalog_revisions", "operations",
        "received_usage", "usage_outbox",
    }
    assert sql("select has_schema_privilege('billing_commerce_runtime','tenant_runtime','usage')") == "t"


def test_runtime_role_can_persist_opaque_state(runtime_database):
    sql = runtime_database
    sql("""
      set role billing_commerce_runtime;
      set search_path=tenant_runtime,public;
      insert into credential_redemptions(token_hash,credential_ref,tenant,expires_at)
      values(repeat('a',64),'credential_ref_opaque','re8ch',4102444800);
      insert into instances(resource_ref,tenant_ref,name,service_class,state,spec_json,
        generation,created_at,updated_at)
      values('resource_ref_opaque','re8ch','commerce','database','Ready','{}',1,
        '2026-11-29T00:00:00Z','2026-11-29T00:00:00Z');
    """)
    assert sql("select tenant from tenant_runtime.credential_redemptions") == "re8ch"
    assert sql("select state from tenant_runtime.instances") == "Ready"


def test_migration_is_bounded_and_contains_no_credential_values():
    migration = MIGRATION.read_text().lower()
    assert migration.count("set local role re8ch_billing_migrator") == 1
    assert "set session authorization" not in migration
    assert "password" not in migration
    assert "token text" not in migration
