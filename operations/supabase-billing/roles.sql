-- Apply to postgres through the reviewed postgres_admin_sql workflow only.
-- Passwords and connection URIs are provisioned through the secret manager.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'billing_studio_admin') then
    create role billing_studio_admin login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'billing_auth_admin') then
    create role billing_auth_admin login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'billing_storage_admin') then
    create role billing_storage_admin login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'billing_realtime_admin') then
    create role billing_realtime_admin login noinherit replication;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'billing_supabase_pgrst_rw') then
    create role billing_supabase_pgrst_rw login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'billing_supabase_pgrst_ro') then
    create role billing_supabase_pgrst_ro login noinherit;
  end if;
end
$$;

grant connect on database re8ch_billing to billing_studio_admin, billing_auth_admin,
  billing_storage_admin, billing_realtime_admin, billing_supabase_pgrst_rw,
  billing_supabase_pgrst_ro;
alter role billing_studio_admin set default_transaction_read_only = on;
alter role billing_supabase_pgrst_rw set default_transaction_isolation = 'serializable';
alter role billing_supabase_pgrst_ro set default_transaction_isolation = 'serializable';
alter role billing_supabase_pgrst_ro set default_transaction_read_only = on;
