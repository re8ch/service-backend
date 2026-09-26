-- Run as supabase_auth_admin against re8ch_identity after GoTrue initializes.
-- The core model references GoTrue UUIDs but does not own auth tables.
grant usage on schema auth to supabase_admin;
grant select, references on auth.users to supabase_admin;
grant select on auth.identities to supabase_admin;
