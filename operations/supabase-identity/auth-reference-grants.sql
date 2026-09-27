-- Run as supabase_auth_admin against re8ch_identity after GoTrue initializes.
-- The core model references GoTrue UUIDs but does not own auth tables.
grant usage on schema auth to supabase_admin;
grant select, references on auth.users to supabase_admin;
grant select on auth.identities to supabase_admin;

-- GoTrue enables RLS on these tables. A SELECT grant alone leaves Studio's
-- supabase_admin connection with an empty user list. Keep this exception
-- read-only and scoped to the Studio database role.
drop policy if exists identity_studio_admin_read on auth.users;
create policy identity_studio_admin_read on auth.users
  for select to supabase_admin using (true);
drop policy if exists identity_studio_admin_read on auth.identities;
create policy identity_studio_admin_read on auth.identities
  for select to supabase_admin using (true);
