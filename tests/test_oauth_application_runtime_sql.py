"""Exercise org-owned OAuth application registration in isolated PostgreSQL."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "operations/supabase-identity/oauth-application-runtime.yaml"
ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def postgres(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="oauth-app-pg-", dir="/tmp"))
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


@pytest.fixture
def oauth_db(postgres):
    postgres(f"""
        create role supabase_admin superuser; create role service_role;
        create role authenticated; create role anon;
        create schema auth; create schema core; create schema api;
        create table auth.users (id uuid primary key, deleted_at timestamptz);
        create table auth.oauth_clients (
          id uuid primary key, client_secret_hash text not null,
          registration_type text, client_type text, token_endpoint_auth_method text,
          redirect_uris text, grant_types text, client_name text, client_uri text,
          deleted_at timestamptz, updated_at timestamptz default now());
        create table core.organizations (tenant_ref text primary key, status text);
        create table core.organization_members (
          tenant_ref text, user_id uuid, status text, role text,
          primary key (tenant_ref,user_id));
        create table core.user_active_organizations (
          user_id uuid primary key, tenant_ref text);
        create function api.current_user_id() returns uuid language sql stable as
          $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
        insert into auth.users values ('{ALICE}',null),('{BOB}',null);
        insert into core.organizations values ('acme','active'),('personal-{ALICE}','active');
        insert into core.organization_members values
          ('acme','{ALICE}','active','member'),
          ('acme','{BOB}','active','member'),
          ('personal-{ALICE}','{ALICE}','active','owner');
        insert into core.user_active_organizations values ('{ALICE}','acme'),('{BOB}','acme');
    """)
    config_map, job = list(yaml.safe_load_all(MANIFEST.read_text()))
    postgres(config_map["data"]["migration.sql"])
    return postgres, config_map, job


def call(sql, user, expression, *, expect_failure=False):
    return sql(f"set role authenticated; set request.jwt.claim.sub='{user}'; select {expression};", expect_failure=expect_failure)


def test_public_registration_is_org_scoped_exact_and_pkce(oauth_db):
    sql, _, _ = oauth_db
    created = call(sql, ALICE, "api.create_oauth_application('Viewer','[\"https://app.example/callback\"]','req-1')->>'ownerOrgRef'")
    assert created == "acme"
    assert sql("select client_type||':'||pkce_required||':'||lifecycle from core.oauth_applications") == "public:true:active"
    assert sql("select token_endpoint_auth_method||':'||client_secret_hash from auth.oauth_clients") == "none:"
    assert sql("select redirect_uri from core.oauth_application_redirect_uris") == "https://app.example/callback"
    error = call(sql, ALICE, "api.create_oauth_application('Bad','[\"https://*.example/cb\"]','bad')", expect_failure=True)
    assert "unique exact HTTPS" in error
    error = call(sql, ALICE, "api.create_oauth_application('Bad','[\"http://app.example/cb\"]','bad-2')", expect_failure=True)
    assert "unique exact HTTPS" in error


def test_confidential_secret_is_hashed_and_returned_only_once(oauth_db):
    sql, _, _ = oauth_db
    secret = call(sql, ALICE, "api.create_oauth_application('Server','[\"https://server.example/cb\"]','req-secret',null,'confidential')->>'clientSecret'")
    assert secret and len(secret) >= 40
    assert sql(f"select client_secret_hash <> '{secret}' and public.crypt('{secret}',client_secret_hash)=client_secret_hash from auth.oauth_clients") == "t"
    replay = call(sql, ALICE, "api.create_oauth_application('Server','[\"https://server.example/cb\"]','req-secret',null,'confidential')")
    assert '"idempotentReplay": true' in replay
    assert "clientSecret\"" not in replay


def test_active_org_personal_org_and_management_boundaries(oauth_db):
    sql, _, _ = oauth_db
    app_id = call(sql, ALICE, "api.create_oauth_application('Shared','[\"https://shared.example/cb\"]','shared')->>'applicationId'")
    denied = call(sql, BOB, f"api.set_oauth_application_lifecycle('{app_id}','suspended')", expect_failure=True)
    assert "management denied" in denied
    sql(f"update core.organization_members set role='admin' where tenant_ref='acme' and user_id='{BOB}'")
    assert '"lifecycle": "suspended"' in call(sql, BOB, f"api.set_oauth_application_lifecycle('{app_id}','suspended')")
    assert sql(f"select deleted_at is not null from auth.oauth_clients where id='{app_id}'") == "t"
    sql(f"update core.user_active_organizations set tenant_ref='personal-{ALICE}' where user_id='{ALICE}'")
    assert call(sql, ALICE, "api.create_oauth_application('Personal','[\"https://me.example/cb\"]','personal')->>'ownerOrgRef'") == f"personal-{ALICE}"
    assert call(sql, ALICE, "count(*) from api.my_oauth_applications()") == "1"


def test_manifest_mount_and_privilege_contract(oauth_db):
    sql, config_map, job = oauth_db
    assert job["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] == config_map["metadata"]["name"]
    assert sql("select relrowsecurity from pg_class where oid='core.oauth_applications'::regclass") == "t"
    assert sql("select has_function_privilege('authenticated','api.create_oauth_application(text,jsonb,text,text,text)','execute')") == "t"
    assert sql("select has_function_privilege('anon','api.create_oauth_application(text,jsonb,text,text,text)','execute')") == "f"
