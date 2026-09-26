-- Run against the Pigsty primary as a database administrator with psql -X
-- and ON_ERROR_STOP=1. This is the fresh GoTrue target; the legacy auth
-- database remains untouched. Run only after verifying the target is absent.
select 'create database re8ch_identity owner supabase_admin'
where not exists (select 1 from pg_database where datname = 're8ch_identity')
\gexec

grant connect, create on database re8ch_identity to supabase_auth_admin;
grant connect, create on database re8ch_identity to supabase_admin;
grant connect on database re8ch_identity to supabase_storage_admin, authenticator;
