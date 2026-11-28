from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operations/supabase-billing"

SERVER = "mcpsrv_" + "1" * 32
TOOL = "mcptool_" + "2" * 32
REVISION = "mcprev_" + "3" * 32
REGISTRATION = "mcpreg_" + "4" * 32
REQUESTOR = "11111111-1111-1111-1111-111111111111"
REVIEWER = "22222222-2222-2222-2222-222222222222"
DIGEST = "a" * 64
SCHEMA_DIGEST = "b" * 64


@pytest.fixture
def catalog_database(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="dynamic-catalog-pg-", dir="/tmp"))
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "--no-instructions"],
                   check=True, capture_output=True, text=True)
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(tmp_path / "postgres.log"),
                    "-o", f"-k {socket} -c listen_addresses=''", "-w", "start"],
                   check=True, capture_output=True, text=True)

    def sql(statement, succeeds=True):
        result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket),
                                 "-d", "postgres", "-Atqc", statement],
                                capture_output=True, text=True)
        if succeeds:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0
        return result.stdout.strip(), result.stderr.strip()

    try:
        sql("""
          create role re8ch_billing_owner;
          create role re8ch_billing_migrator;
          create role billing_commerce_runtime;
          create role billing_supabase_pgrst_rw login;
          create role billing_supabase_pgrst_ro login;
          grant re8ch_billing_owner to re8ch_billing_migrator;
          grant billing_commerce_runtime to re8ch_billing_migrator with admin option;
          alter database postgres owner to re8ch_billing_owner;
        """)
        sql((BASE / "dynamic-mcp-catalog-authority.sql").read_text())
        sql(f"""
          insert into bifurcate.mcp_servers(server_ref, owner_org_ref, slug, display_name,
            service_instance_ref, lifecycle, created_by_subject)
          values('{SERVER}', 'org_one', 'catalog', 'Catalog', 'toolhive/instance', 'active',
            '{REQUESTOR}');
          insert into bifurcate.mcp_tools(tool_ref, server_ref, owner_org_ref, tool_name,
            created_by_subject)
          values('{TOOL}', '{SERVER}', 'org_one', 'tenant.list', '{REQUESTOR}');
          insert into bifurcate.mcp_server_revisions(revision_ref, server_ref, owner_org_ref,
            revision_number, definition, definition_sha256, created_by_subject)
          values('{REVISION}', '{SERVER}', 'org_one', 1, '{{}}', '{DIGEST}', '{REQUESTOR}');
          insert into bifurcate.mcp_revision_tools(revision_ref, server_ref, owner_org_ref,
            tool_ref, ordinal, input_schema, tool_definition, schema_sha256)
          values('{REVISION}', '{SERVER}', 'org_one', '{TOOL}', 0,
            '{{"type":"object"}}', '{{"name":"tenant.list"}}', '{SCHEMA_DIGEST}');
        """)
        yield sql
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"],
                       check=True, capture_output=True, text=True)
        socket.rmdir()


def request_registration(sql):
    return sql(f"""
      select * from bifurcate_api.request_mcp_catalog_registration(
        '{REGISTRATION}', 'org_one', '{SERVER}', '{REVISION}', '{REQUESTOR}',
        'create-catalog', '{DIGEST}', 'oauthcl_abcdefghijklmnop');
    """)[0]


def test_registration_is_idempotent_and_outbox_is_transactional(catalog_database):
    sql = catalog_database
    assert request_registration(sql) == f"{REGISTRATION}|requested|f"
    assert request_registration(sql) == f"{REGISTRATION}|requested|t"
    assert sql("select event_type from bifurcate.mcp_catalog_outbox order by event_id")[0] == \
        "catalog.registration.requested"
    _, error = sql(f"""
      select * from bifurcate_api.request_mcp_catalog_registration(
        '{REGISTRATION}', 'org_one', '{SERVER}', '{REVISION}', '{REQUESTOR}',
        'create-catalog', '{'c' * 64}', 'oauthcl_abcdefghijklmnop');
    """, succeeds=False)
    assert "different registration content" in error


def test_publication_requires_separation_of_duties_and_sealed_revision(catalog_database):
    sql = catalog_database
    request_registration(sql)
    sql(f"select * from bifurcate_api.transition_mcp_catalog_registration('{REGISTRATION}',"
        f"'requested','validated','{REQUESTOR}',null)")
    _, error = sql(f"select * from bifurcate_api.transition_mcp_catalog_registration("
                   f"'{REGISTRATION}','validated','approved','{REQUESTOR}',null)", succeeds=False)
    assert "different subject" in error
    sql(f"select * from bifurcate_api.transition_mcp_catalog_registration('{REGISTRATION}',"
        f"'validated','approved','{REVIEWER}',null)")
    _, error = sql(f"select * from bifurcate_api.transition_mcp_catalog_registration("
                   f"'{REGISTRATION}','approved','published','{REVIEWER}',null)", succeeds=False)
    assert "sealed revision" in error
    sql(f"update bifurcate.mcp_server_revisions set state='sealed', sealed_at=now() "
        f"where revision_ref='{REVISION}'")
    assert sql(f"select * from bifurcate_api.transition_mcp_catalog_registration("
               f"'{REGISTRATION}','approved','published','{REVIEWER}',null)")[0] == \
        f"{REGISTRATION}|published"


def test_activation_exposes_one_atomic_revision_and_preserves_history(catalog_database):
    sql = catalog_database
    request_registration(sql)
    sql(f"update bifurcate.mcp_server_revisions set state='sealed', sealed_at=now() "
        f"where revision_ref='{REVISION}'")
    sql(f"select * from bifurcate_api.transition_mcp_catalog_registration('{REGISTRATION}',"
        f"'requested','validated','{REQUESTOR}',null)")
    sql(f"select * from bifurcate_api.transition_mcp_catalog_registration('{REGISTRATION}',"
        f"'validated','approved','{REVIEWER}',null)")
    sql(f"select * from bifurcate_api.transition_mcp_catalog_registration('{REGISTRATION}',"
        f"'approved','published','{REVIEWER}',null)")
    assert sql(f"select * from bifurcate_api.activate_mcp_catalog_revision("
               f"'{REGISTRATION}','{REVIEWER}')")[0] == f"{SERVER}|{REVISION}|1"
    _, error = sql(f"select * from bifurcate_api.activate_mcp_catalog_revision("
                   f"'{REGISTRATION}','{REVIEWER}',null)", succeeds=False)
    assert "active MCP revision changed" in error
    assert sql("select tool_name || '|' || schema_sha256 from bifurcate.active_mcp_catalog")[0] == \
        f"tenant.list|{SCHEMA_DIGEST}"
    assert sql("select count(*) from bifurcate.mcp_catalog_outbox")[0] == "5"
    _, error = sql(f"update bifurcate.mcp_revision_tools set ordinal=1 "
                   f"where revision_ref='{REVISION}'", succeeds=False)
    assert "sealed MCP revision tools are immutable" in error


def test_runtime_role_only_sees_bounded_projection_and_rpcs(catalog_database):
    sql = catalog_database
    assert sql("select has_table_privilege('billing_commerce_runtime',"
               "'bifurcate.active_mcp_catalog','select')")[0] == "t"
    assert sql("select has_table_privilege('billing_commerce_runtime',"
               "'bifurcate.mcp_catalog_registrations','select')")[0] == "f"
    assert sql("select has_function_privilege('billing_commerce_runtime',"
               "'bifurcate_api.request_mcp_catalog_registration(text,text,text,text,uuid,text,text,text)',"
               "'execute')")[0] == "t"

