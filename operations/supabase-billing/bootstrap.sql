-- Apply to re8ch_billing through the reviewed postgres_admin_sql workflow only.
create schema if not exists auth authorization billing_auth_admin;
create schema if not exists storage authorization billing_storage_admin;
create schema if not exists _realtime authorization billing_realtime_admin;

grant usage, create on schema public, auth to billing_auth_admin;
grant usage, create on schema public, storage to billing_storage_admin;
grant usage, create on schema _realtime to billing_realtime_admin;
grant usage on schema payment_api, commerce_api to billing_supabase_pgrst_rw,
  billing_supabase_pgrst_ro;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'payment_app') then
    grant payment_app to billing_supabase_pgrst_rw;
  end if;
  if exists (select 1 from pg_roles where rolname = 'payment_app_ro') then
    grant payment_app_ro to billing_supabase_pgrst_rw, billing_supabase_pgrst_ro;
  end if;
  if exists (select 1 from pg_roles where rolname = 'commerce_runtime') then
    grant commerce_runtime to billing_supabase_pgrst_rw, billing_supabase_pgrst_ro;
  end if;
end
$$;

grant usage on schema payment, commerce, billing, omni_ledger to billing_studio_admin;
grant select on all tables in schema payment, commerce, billing, omni_ledger to billing_studio_admin;
grant select on all sequences in schema payment, commerce, billing, omni_ledger to billing_studio_admin;
