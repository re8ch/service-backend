-- Run as supabase_admin immediately before the one-shot importer.
-- The importer uses supabase_auth_admin; RLS otherwise hides inserted rows
-- from its count reconciliation and rejects organization inserts.
create policy identity_import_org on core.organizations
  for insert to supabase_auth_admin with check (true);
create policy identity_import_member on core.organization_members
  for insert to supabase_auth_admin with check (true);
create policy identity_import_org_read on core.organizations
  for select to supabase_auth_admin using (true);
create policy identity_import_member_read on core.organization_members
  for select to supabase_auth_admin using (true);
