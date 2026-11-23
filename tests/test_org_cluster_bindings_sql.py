"""Exercise organization-owned cluster binding authority in isolated PostgreSQL."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/supabase-billing/org-cluster-bindings.sql"
ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@pytest.fixture
def postgres(tmp_path):
    if not all(shutil.which(name) for name in ("initdb", "pg_ctl", "psql")):
        pytest.skip("local PostgreSQL binaries unavailable")
    data = tmp_path / "data"
    socket = Path(tempfile.mkdtemp(prefix="cluster-binding-pg-", dir="/tmp"))
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
def binding_db(postgres):
    postgres("""
      create role re8ch_billing_migrator;
      create role authenticated;
      create schema billing authorization re8ch_billing_migrator;
      create schema commerce_api authorization re8ch_billing_migrator;
      create extension pgcrypto;
      grant usage on schema commerce_api to authenticated;
    """)
    postgres(MIGRATION.read_text())
    return postgres


def claims(subject, org, role="owner", state="active", cluster_ref=None, digest=None):
    value = {"sub": subject, "org_ref": org, "org_role": role, "org_membership_state": state}
    if cluster_ref:
        value.update({"cluster_binding_action": "register", "cluster_ref": cluster_ref, "cluster_binding_digest": digest})
    return json.dumps(value, separators=(",", ":"))


def call(sql, token_claims, expression, *, expect_failure=False):
    escaped = token_claims.replace("'", "''")
    return sql(f"set role authenticated; set request.jwt.claims='{escaped}'; select {expression};", expect_failure=expect_failure)


def test_binding_uses_claimed_active_org_and_personal_org(binding_db):
    sql = binding_db
    result = call(sql, claims(ALICE, "acme", cluster_ref="cluster/acme", digest=DIGEST_A), f"commerce_api.register_active_org_cluster_binding('cluster/acme','operator','{DIGEST_A}','op-1')")
    assert '"orgRef": "acme"' in result and '"lifecycle": "Ready"' in result
    assert call(sql, claims(ALICE, "acme", "member"), "commerce_api.verify_active_org_cluster_binding('cluster/acme','operator')->>'bindingRole'") == "operator"
    denied = call(sql, claims(BOB, "other", "owner"), "commerce_api.verify_active_org_cluster_binding('cluster/acme','viewer')", expect_failure=True)
    assert "no Ready cluster binding" in denied
    personal = call(sql, claims(ALICE, f"personal-{ALICE}", cluster_ref="cluster/personal", digest=DIGEST_B), f"commerce_api.register_active_org_cluster_binding('cluster/personal','owner','{DIGEST_B}','personal-1')->>'orgRef'")
    assert personal == f"personal-{ALICE}"


def test_membership_and_binding_role_fail_closed(binding_db):
    sql = binding_db
    call(sql, claims(ALICE, "acme", cluster_ref="cluster/view", digest=DIGEST_A), f"commerce_api.register_active_org_cluster_binding('cluster/view','viewer','{DIGEST_A}','view-1')")
    insufficient = call(sql, claims(ALICE, "acme", "member"), "commerce_api.verify_active_org_cluster_binding('cluster/view','operator')", expect_failure=True)
    assert "role is insufficient" in insufficient
    revoked = call(sql, claims(ALICE, "acme", state="revoked"), "commerce_api.verify_active_org_cluster_binding('cluster/view','viewer')", expect_failure=True)
    assert "active organization membership required" in revoked
    viewer_write = call(sql, claims(BOB, "acme", "viewer", cluster_ref="cluster/nope", digest=DIGEST_B), f"commerce_api.register_active_org_cluster_binding('cluster/nope','viewer','{DIGEST_B}','nope')", expect_failure=True)
    assert "owner or admin required" in viewer_write

    owner_token = claims(ALICE, "acme", cluster_ref="cluster/owner", digest=DIGEST_B)
    call(sql, owner_token, f"commerce_api.register_active_org_cluster_binding('cluster/owner','owner','{DIGEST_B}','owner-1')")
    viewer_operator = call(sql, claims(BOB, "acme", "viewer"), "commerce_api.verify_active_org_cluster_binding('cluster/owner','operator')", expect_failure=True)
    assert "role is insufficient" in viewer_operator
    viewer_owner = call(sql, claims(BOB, "acme", "viewer"), "commerce_api.verify_active_org_cluster_binding('cluster/owner','owner')", expect_failure=True)
    assert "role is insufficient" in viewer_owner
    member_owner = call(sql, claims(BOB, "acme", "member"), "commerce_api.verify_active_org_cluster_binding('cluster/owner','owner')", expect_failure=True)
    assert "role is insufficient" in member_owner
    assert call(sql, claims(BOB, "acme", "member"), "commerce_api.verify_active_org_cluster_binding('cluster/owner','operator')->>'effectiveRole'") == "operator"


def test_idempotency_digest_guard_and_revocation(binding_db):
    sql = binding_db
    token = claims(ALICE, "acme", cluster_ref="cluster/prod", digest=DIGEST_A)
    call(sql, token, f"commerce_api.register_active_org_cluster_binding('cluster/prod','owner','{DIGEST_A}','prod-1')")
    replay = call(sql, token, f"commerce_api.register_active_org_cluster_binding('cluster/prod','owner','{DIGEST_A}','prod-1')")
    assert '"idempotentReplay": true' in replay
    mismatch = call(sql, token, f"commerce_api.register_active_org_cluster_binding('cluster/prod','viewer','{DIGEST_A}','prod-1')", expect_failure=True)
    assert "different cluster binding request" in mismatch
    unattested = call(sql, claims(ALICE, "acme"), f"commerce_api.register_active_org_cluster_binding('cluster/rogue','owner','{DIGEST_B}','rogue')", expect_failure=True)
    assert "provider-attested cluster binding claims required" in unattested
    wrong_digest = call(sql, token, f"commerce_api.revoke_active_org_cluster_binding('cluster/prod','{DIGEST_B}')", expect_failure=True)
    assert "expected digest not found" in wrong_digest
    assert '"lifecycle": "revoked"' in call(sql, token, f"commerce_api.revoke_active_org_cluster_binding('cluster/prod','{DIGEST_A}')")
    denied = call(sql, token, "commerce_api.verify_active_org_cluster_binding('cluster/prod','viewer')", expect_failure=True)
    assert "no Ready cluster binding" in denied


def test_table_is_not_directly_exposed(binding_db):
    sql = binding_db
    assert sql("select relrowsecurity from pg_class where oid='billing.org_cluster_bindings'::regclass") == "t"
    assert sql("select has_table_privilege('authenticated','billing.org_cluster_bindings','select')") == "f"
    assert sql("select has_function_privilege('authenticated','commerce_api.verify_active_org_cluster_binding(text,text)','execute')") == "t"
