-- Apply to re8ch_identity as supabase_admin after the core identity migration.
-- Provider passwords and tokens remain in the Kubernetes credential broker.
begin;

create table if not exists core.tenant_service_configs (
  tenant_ref text not null references core.organizations(tenant_ref) on delete restrict,
  service_class text not null check (service_class in ('registry.harbor.v1', 'observability.grafana.v1')),
  instance_uid text not null,
  instance_name text not null,
  identity_user_id uuid not null references auth.users(id) on delete restrict,
  provider_user_id text,
  provider_resource_ref text,
  desired_config jsonb not null default '{}'::jsonb,
  observed_config jsonb not null default '{}'::jsonb,
  credential_ref text,
  lifecycle text not null check (lifecycle in ('Requested', 'Suspended', 'Deleting')),
  observed_phase text not null,
  observed_generation bigint not null,
  updated_at timestamptz not null default now(),
  primary key (tenant_ref, service_class, instance_uid),
  constraint no_inline_provider_credentials check (
    not (desired_config ?| array['password', 'token', 'secret', 'apiKey'])
    and not (observed_config ?| array['password', 'token', 'secret', 'apiKey'])
  )
);
create index if not exists tenant_service_configs_name_idx
  on core.tenant_service_configs (tenant_ref, instance_name);
alter table core.tenant_service_configs enable row level security;
revoke all on core.tenant_service_configs from public, anon, authenticated;
grant usage on schema api to service_role;
grant usage on schema api to authenticated;

create or replace function api.upsert_tenant_service_config(
  p_tenant_ref text, p_service_class text, p_instance_uid text,
  p_instance_name text, p_identity_user_id uuid, p_provider_user_id text,
  p_provider_resource_ref text,
  p_desired_config jsonb, p_observed_config jsonb,
  p_credential_ref text, p_lifecycle text, p_observed_phase text,
  p_observed_generation bigint
) returns void
language plpgsql security definer set search_path = pg_catalog, pg_temp
as $$
begin
  if p_lifecycle = 'Requested' and not exists (
    select 1 from core.organization_members m
    join core.organizations o on o.tenant_ref = m.tenant_ref
    join auth.users u on u.id = m.user_id
    where m.tenant_ref = p_tenant_ref and m.user_id = p_identity_user_id
      and m.status = 'active' and m.role in ('owner', 'admin', 'member')
      and o.status = 'active' and u.deleted_at is null
  ) then
    raise exception 'Supabase user is not an active tenant member' using errcode = '42501';
  end if;
  if nullif(p_provider_user_id, '') is not null then
    insert into core.product_accounts (product, product_user_id, user_id, tenant_ref, metadata)
    values (p_service_class, p_provider_user_id, p_identity_user_id, p_tenant_ref,
      jsonb_build_object('providerResourceRef', p_provider_resource_ref))
    on conflict (product, product_user_id) do update set
      metadata = excluded.metadata, updated_at = now()
    where core.product_accounts.user_id = excluded.user_id
      and core.product_accounts.tenant_ref = excluded.tenant_ref;
    if not found then
      raise exception 'provider account is bound to another identity' using errcode = '42501';
    end if;
  end if;
  insert into core.tenant_service_configs (
    tenant_ref, service_class, instance_uid, instance_name, identity_user_id,
    provider_user_id, provider_resource_ref,
    desired_config, observed_config, credential_ref, lifecycle,
    observed_phase, observed_generation
  ) values (
    p_tenant_ref, p_service_class, p_instance_uid, p_instance_name,
    p_identity_user_id, p_provider_user_id,
    p_provider_resource_ref, p_desired_config, p_observed_config,
    p_credential_ref, p_lifecycle, p_observed_phase, p_observed_generation
  ) on conflict (tenant_ref, service_class, instance_uid) do update set
    instance_name = excluded.instance_name,
    identity_user_id = excluded.identity_user_id,
    provider_user_id = excluded.provider_user_id,
    provider_resource_ref = excluded.provider_resource_ref,
    desired_config = excluded.desired_config,
    observed_config = excluded.observed_config,
    credential_ref = excluded.credential_ref,
    lifecycle = excluded.lifecycle,
    observed_phase = excluded.observed_phase,
    observed_generation = excluded.observed_generation,
    updated_at = now()
    where core.tenant_service_configs.identity_user_id = excluded.identity_user_id;
  if not found then
    raise exception 'service instance is bound to another identity' using errcode = '42501';
  end if;
end
$$;
revoke all on function api.upsert_tenant_service_config(text,text,text,text,uuid,text,text,jsonb,jsonb,text,text,text,bigint) from public, anon, authenticated;
grant execute on function api.upsert_tenant_service_config(text,text,text,text,uuid,text,text,jsonb,jsonb,text,text,text,bigint) to service_role;

create or replace function api.resolve_tenant_service_user(p_tenant_ref text, p_user_id uuid)
returns table(user_id uuid, email text)
language sql stable security definer set search_path = pg_catalog, pg_temp
as $$
  select u.id, u.email::text
  from auth.users u
  join core.organization_members m on m.user_id = u.id
  join core.organizations o on o.tenant_ref = m.tenant_ref
  where u.id = p_user_id and m.tenant_ref = p_tenant_ref
    and m.status = 'active' and m.role in ('owner', 'admin', 'member')
    and o.status = 'active' and u.deleted_at is null
$$;
revoke all on function api.resolve_tenant_service_user(text,uuid) from public, anon, authenticated;
grant execute on function api.resolve_tenant_service_user(text,uuid) to service_role;

create or replace function api.my_tenant_service_configs()
returns table(tenant_ref text, service_class text, instance_name text,
  provider_resource_ref text, desired_config jsonb, observed_config jsonb,
  lifecycle text, observed_phase text, updated_at timestamptz)
language sql stable security definer set search_path = pg_catalog, pg_temp
as $$
  select c.tenant_ref, c.service_class, c.instance_name, c.provider_resource_ref,
    c.desired_config, c.observed_config, c.lifecycle, c.observed_phase, c.updated_at
  from core.tenant_service_configs c
  join core.organization_members m on m.tenant_ref = c.tenant_ref
  join core.organizations o on o.tenant_ref = c.tenant_ref
  where m.user_id = api.current_user_id()
    and c.identity_user_id = m.user_id
    and m.status = 'active' and o.status = 'active'
$$;
revoke all on function api.my_tenant_service_configs() from public, anon;
grant execute on function api.my_tenant_service_configs() to authenticated;
alter table core.tenant_service_configs owner to supabase_admin;
alter function api.upsert_tenant_service_config(text,text,text,text,uuid,text,text,jsonb,jsonb,text,text,text,bigint) owner to supabase_admin;
alter function api.resolve_tenant_service_user(text,uuid) owner to supabase_admin;
alter function api.my_tenant_service_configs() owner to supabase_admin;
commit;
