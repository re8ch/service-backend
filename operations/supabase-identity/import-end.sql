-- Run as supabase_admin after successful count reconciliation, or on failure.
drop policy if exists identity_import_org on core.organizations;
drop policy if exists identity_import_member on core.organization_members;
drop policy if exists identity_import_org_read on core.organizations;
drop policy if exists identity_import_member_read on core.organization_members;
revoke select, insert on core.legacy_user_ids, core.organizations,
  core.organization_members, core.product_accounts,
  core.legacy_external_identities from supabase_auth_admin;
revoke usage on schema core from supabase_auth_admin;
