from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "operations/supabase-billing/tool-publication-authority.sql").read_text()
DOC = (ROOT / "operations/supabase-billing/TOOL-PUBLICATION-AUTHORITY.md").read_text()


def test_contract_has_complete_org_owned_authority_chain():
    for table in ("tool_publications", "tool_revisions", "tool_audiences",
                  "tool_installations", "tool_bindings", "usage_events"):
        assert f"bifurcate.{table}" in SQL
    assert "owner_org_ref text not null" in SQL
    assert "installed_org_ref text not null" in SQL
    assert "target_org_ref text not null" in SQL
    assert "A Personal\nOrganization is an ordinary organization reference" in DOC


def test_audiences_are_explicit_and_admin_activation_is_fail_closed():
    assert "target_org_ref <> '*'" in SQL
    assert "audience_kind in ('organization','admin')" in SQL
    assert "default 'pending_approval'" in SQL
    assert "admin_audience_starts_pending before insert" in SQL
    assert "new.state := 'pending_approval'" in SQL
    assert "approved_by_subject is not null and approved_at is not null" in SQL
    assert "approved_by_subject <> requested_by_subject" in SQL
    assert "implicit public visibility" in DOC


def test_provider_tools_only_project_fully_ready_bindings():
    assert "create or replace view bifurcate.ready_tool_bindings" in SQL
    for predicate in ("b.state = 'ready'", "i.state = 'installed'",
                      "a.state = 'active'", "r.lifecycle = 'published'",
                      "p.lifecycle = 'published'"):
        assert predicate in SQL
    assert "credential_material" not in SQL.lower()


def test_usage_ingress_is_unpriced_and_content_safe_on_replay():
    assert "unique (producer_ref, idempotency_key)" in SQL
    assert "pricing_applied boolean not null default false check (pricing_applied = false)" in SQL
    assert "accounting_posted boolean not null default false check (accounting_posted = false)" in SQL
    assert "v_existing.content_sha256 is distinct from p_content_sha256" in SQL
    assert "idempotency key already exists with different content" in SQL


def test_usage_replay_compares_every_business_field_without_trusting_digest():
    replay_guard = SQL.split("The caller-provided digest is audit metadata", 1)[1].split(
        "raise exception 'idempotency key already exists with different content'", 1
    )[0]
    for field in ("event_ref", "owner_org_ref", "resource_ref", "service_class",
                  "meter_code", "quantity", "unit", "period_start", "period_end",
                  "observed_at", "dimensions", "schema_version", "content_sha256"):
        assert f"v_existing.{field} is distinct from" in replay_guard
    assert "database does not trust it as replay proof" in DOC


def test_usage_replay_rejects_same_key_event_and_hash_when_payload_changes():
    # These are the security regression dimensions: each must participate in
    # the database comparison even if event_ref and content_sha256 are reused.
    replay_guard = SQL.split("The caller-provided digest is audit metadata", 1)[1].split(
        "raise exception 'idempotency key already exists with different content'", 1
    )[0]
    for changed_field in ("quantity", "owner_org_ref", "resource_ref",
                          "period_start", "period_end", "dimensions"):
        assert f"v_existing.{changed_field} is distinct from" in replay_guard


def test_usage_security_definer_is_narrow_and_fail_closed():
    function = SQL.split("create or replace function bifurcate.accept_usage_event", 1)[1]
    function = function.split("create or replace function bifurcate.reject_revision_mutation", 1)[0]
    assert "language plpgsql security definer" in function
    assert "set search_path = bifurcate, pg_catalog, pg_temp" in function
    assert "revoke all on all functions in schema bifurcate from public" in SQL
    assert "grant execute on function bifurcate.accept_usage_event" in SQL


def test_contract_does_not_create_identity_or_oauth_state():
    lowered = SQL.lower()
    for forbidden in ("oauth_client", "access_token", "refresh_token", "user_profile",
                      "organization_membership", "create table auth."):
        assert forbidden not in lowered
    assert "re8ch_billing" in SQL
    assert "pg-identity" in DOC
