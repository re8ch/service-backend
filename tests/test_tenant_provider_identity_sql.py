"""Exercise tenant provider identity isolation against an isolated PostgreSQL."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/supabase-identity/tenant-provider-config.sql"
ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def postgres(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="tenant-pg-", dir="/tmp"))
    subprocess.run(["initdb", "-D", str(data), "-A", "trust", "--no-instructions"], check=True, capture_output=True, text=True)
    subprocess.run(["pg_ctl", "-D", str(data), "-l", str(tmp_path / "postgres.log"), "-o", f"-k {socket} -c listen_addresses=''", "-w", "start"], check=True, capture_output=True, text=True)

    def sql(statement, *, expect_failure=False):
        result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket), "-d", "postgres", "-Atqc", statement], capture_output=True, text=True)
        if expect_failure:
            assert result.returncode != 0, result.stdout
            return result.stderr
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        yield sql
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"], check=True, capture_output=True, text=True)
        socket.rmdir()


def test_user_only_sees_own_config_and_instance_cannot_rebind(postgres):
    postgres("""
        create role supabase_admin superuser;
        create role service_role; create role authenticated; create role anon;
        create schema auth; create schema core; create schema api;
        create table auth.users (id uuid primary key, email text, deleted_at timestamptz);
        create table core.organizations (tenant_ref text primary key, status text);
        create table core.organization_members (tenant_ref text, user_id uuid, status text, role text);
        create table core.product_accounts (product text, product_user_id text, user_id uuid,
            tenant_ref text, metadata jsonb, updated_at timestamptz default now(),
            primary key (product, product_user_id));
        create function api.current_user_id() returns uuid language sql stable as
            $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
        insert into auth.users values
            ('11111111-1111-4111-8111-111111111111', 'alice@example.test', null),
            ('22222222-2222-4222-8222-222222222222', 'bob@example.test', null);
        insert into core.organizations values ('acme', 'active');
        insert into core.organization_members values
            ('acme', '11111111-1111-4111-8111-111111111111', 'active', 'member'),
            ('acme', '22222222-2222-4222-8222-222222222222', 'active', 'member');
    """)
    postgres(MIGRATION.read_text())

    def upsert(user, name, uid):
        return f"""
            set role service_role;
            select api.upsert_tenant_service_config('acme', 'registry.harbor.v1', '{uid}',
                '{name}', '{user}', 'harbor-{name}', '{name}', '{{}}', '{{}}',
                'credential://test/{name}', 'Requested', 'Ready', 1);
        """

    postgres(upsert(ALICE, "alice-registry", "instance-1"))
    postgres(upsert(BOB, "bob-registry", "instance-2"))
    for user, own, other in ((ALICE, "alice-registry", "bob-registry"), (BOB, "bob-registry", "alice-registry")):
        rows = postgres(f"set role authenticated; set request.jwt.claim.sub = '{user}'; select instance_name from api.my_tenant_service_configs();")
        assert rows == own and other not in rows
    error = postgres(upsert(BOB, "stolen-registry", "instance-1"), expect_failure=True)
    assert "service instance is bound to another identity" in error
    assert postgres("select identity_user_id from core.tenant_service_configs where instance_uid = 'instance-1'") == ALICE
