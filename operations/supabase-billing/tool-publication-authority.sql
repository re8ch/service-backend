-- Apply to re8ch_billing through the reviewed postgres_admin_sql workflow only.
-- This is Bifurcate/Catalog business state. It deliberately contains no OAuth
-- client, user, session, credential, or organization-membership tables.
begin transaction isolation level serializable;

create schema if not exists bifurcate authorization re8ch_billing_owner;
set local role re8ch_billing_migrator;

create table if not exists bifurcate.tool_publications (
  publication_ref text primary key check (publication_ref ~ '^toolpub_[a-z0-9][a-z0-9_-]{7,95}$'),
  owner_org_ref text not null check (owner_org_ref <> '*' and owner_org_ref ~ '^[a-z0-9][a-z0-9_-]{0,127}$'),
  slug text not null check (slug ~ '^[a-z][a-z0-9-]{0,62}$'),
  display_name text not null check (length(display_name) between 1 and 200),
  lifecycle text not null default 'draft' check (lifecycle in ('draft','published','suspended','retired')),
  created_by_subject uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_org_ref, slug),
  unique (publication_ref, owner_org_ref)
);

create table if not exists bifurcate.tool_revisions (
  revision_ref text primary key check (revision_ref ~ '^toolrev_[a-f0-9]{32}$'),
  publication_ref text not null references bifurcate.tool_publications(publication_ref),
  revision_number bigint not null check (revision_number > 0),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  definition_sha256 text not null check (definition_sha256 ~ '^[a-f0-9]{64}$'),
  lifecycle text not null default 'draft' check (lifecycle in ('draft','published','withdrawn')),
  created_by_subject uuid not null,
  created_at timestamptz not null default now(),
  unique (publication_ref, revision_number),
  unique (publication_ref, revision_ref),
  unique (revision_ref, definition_sha256)
);

create table if not exists bifurcate.tool_audiences (
  audience_ref text primary key check (audience_ref ~ '^toolaud_[a-f0-9]{32}$'),
  publication_ref text not null references bifurcate.tool_publications(publication_ref),
  target_org_ref text not null check (target_org_ref <> '*' and target_org_ref ~ '^[a-z0-9][a-z0-9_-]{0,127}$'),
  audience_kind text not null check (audience_kind in ('organization','admin')),
  state text not null default 'pending_approval' check (state in ('pending_approval','active','revoked')),
  requested_by_subject uuid not null,
  approved_by_subject uuid,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique (publication_ref, target_org_ref),
  unique (audience_ref, publication_ref, target_org_ref),
  check ((state <> 'active') or (approved_by_subject is not null and approved_at is not null)),
  check ((audience_kind <> 'admin') or state <> 'active' or approved_by_subject <> requested_by_subject),
  check ((state <> 'revoked') or revoked_at is not null)
);

create or replace function bifurcate.force_admin_audience_pending() returns trigger
language plpgsql set search_path = pg_catalog, pg_temp as $$
begin
  if new.audience_kind = 'admin' then
    new.state := 'pending_approval';
    new.approved_by_subject := null;
    new.approved_at := null;
  end if;
  return new;
end;
$$;
drop trigger if exists admin_audience_starts_pending on bifurcate.tool_audiences;
create trigger admin_audience_starts_pending before insert on bifurcate.tool_audiences
for each row execute function bifurcate.force_admin_audience_pending();

create table if not exists bifurcate.tool_installations (
  installation_ref text primary key check (installation_ref ~ '^toolins_[a-f0-9]{32}$'),
  audience_ref text not null,
  publication_ref text not null,
  installed_org_ref text not null check (installed_org_ref <> '*'),
  revision_ref text not null,
  state text not null default 'pending' check (state in ('pending','installed','suspended','removed')),
  installed_by_subject uuid not null,
  installed_at timestamptz,
  removed_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (audience_ref, publication_ref, installed_org_ref)
    references bifurcate.tool_audiences(audience_ref, publication_ref, target_org_ref),
  foreign key (publication_ref, revision_ref)
    references bifurcate.tool_revisions(publication_ref, revision_ref),
  unique (publication_ref, installed_org_ref),
  unique (installation_ref, installed_org_ref),
  check ((state <> 'installed') or installed_at is not null),
  check ((state <> 'removed') or removed_at is not null)
);

create table if not exists bifurcate.tool_bindings (
  binding_ref text primary key check (binding_ref ~ '^toolbind_[a-f0-9]{32}$'),
  installation_ref text not null,
  owner_org_ref text not null check (owner_org_ref <> '*'),
  service_instance_ref text not null,
  service_binding_ref text not null,
  provider_kind text not null,
  state text not null default 'pending' check (state in ('pending','ready','failed','revoked')),
  ready_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (installation_ref, owner_org_ref)
    references bifurcate.tool_installations(installation_ref, installed_org_ref),
  unique (installation_ref, service_binding_ref),
  check ((state <> 'ready') or ready_at is not null),
  check ((state <> 'revoked') or revoked_at is not null)
);

create or replace view bifurcate.ready_tool_bindings as
select b.binding_ref, b.installation_ref, b.owner_org_ref, b.service_instance_ref,
       b.service_binding_ref, b.provider_kind, i.publication_ref, i.revision_ref
from bifurcate.tool_bindings b
join bifurcate.tool_installations i using (installation_ref)
join bifurcate.tool_audiences a using (audience_ref, publication_ref)
join bifurcate.tool_revisions r using (revision_ref, publication_ref)
join bifurcate.tool_publications p using (publication_ref)
where b.state = 'ready' and i.state = 'installed' and a.state = 'active'
  and r.lifecycle = 'published' and p.lifecycle = 'published';

create table if not exists bifurcate.usage_events (
  event_ref text primary key check (event_ref ~ '^ue_[a-f0-9]{32}$'),
  producer_ref text not null check (length(producer_ref) between 1 and 200),
  idempotency_key text not null check (length(idempotency_key) between 1 and 200),
  owner_org_ref text not null check (owner_org_ref <> '*'),
  resource_ref text not null,
  service_class text not null,
  meter_code text not null,
  quantity numeric not null check (quantity >= 0),
  unit text not null,
  period_start timestamptz not null,
  period_end timestamptz not null,
  observed_at timestamptz not null,
  dimensions jsonb not null default '{}'::jsonb check (jsonb_typeof(dimensions) = 'object'),
  schema_version text not null check (schema_version = 'usage.re8ch.com/v1'),
  content_sha256 text not null check (content_sha256 ~ '^[a-f0-9]{64}$'),
  pricing_applied boolean not null default false check (pricing_applied = false),
  accounting_posted boolean not null default false check (accounting_posted = false),
  received_at timestamptz not null default now(),
  unique (producer_ref, idempotency_key),
  check (period_start < period_end)
);

create or replace function bifurcate.accept_usage_event(
  p_event_ref text, p_producer_ref text, p_idempotency_key text,
  p_owner_org_ref text, p_resource_ref text, p_service_class text,
  p_meter_code text, p_quantity numeric, p_unit text,
  p_period_start timestamptz, p_period_end timestamptz, p_observed_at timestamptz,
  p_dimensions jsonb, p_schema_version text, p_content_sha256 text
) returns table(event_ref text, idempotent_replay boolean)
language plpgsql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
declare v_existing bifurcate.usage_events%rowtype;
begin
  insert into bifurcate.usage_events(event_ref, producer_ref, idempotency_key,
    owner_org_ref, resource_ref, service_class, meter_code, quantity, unit,
    period_start, period_end, observed_at, dimensions, schema_version, content_sha256)
  values(p_event_ref, p_producer_ref, p_idempotency_key, p_owner_org_ref,
    p_resource_ref, p_service_class, p_meter_code, p_quantity, p_unit,
    p_period_start, p_period_end, p_observed_at, coalesce(p_dimensions, '{}'::jsonb),
    p_schema_version, p_content_sha256)
  on conflict (producer_ref, idempotency_key) do nothing
  returning * into v_existing;
  if found then
    return query select p_event_ref, false;
    return;
  end if;
  select * into strict v_existing from bifurcate.usage_events
    where producer_ref = p_producer_ref and idempotency_key = p_idempotency_key;
  if v_existing.event_ref <> p_event_ref or v_existing.content_sha256 <> p_content_sha256 then
    raise exception 'idempotency key already exists with different content'
      using errcode = '23505';
  end if;
  return query select v_existing.event_ref, true;
end;
$$;

create or replace function bifurcate.reject_revision_mutation() returns trigger
language plpgsql set search_path = pg_catalog, pg_temp as $$
begin
  if old.definition is distinct from new.definition
     or old.definition_sha256 is distinct from new.definition_sha256
     or old.publication_ref is distinct from new.publication_ref
     or old.revision_number is distinct from new.revision_number then
    raise exception 'tool revision payloads are immutable' using errcode = '55000';
  end if;
  return new;
end;
$$;
drop trigger if exists tool_revision_payload_immutable on bifurcate.tool_revisions;
create trigger tool_revision_payload_immutable before update on bifurcate.tool_revisions
for each row execute function bifurcate.reject_revision_mutation();

revoke all on schema bifurcate from public;
revoke all on all tables in schema bifurcate from public;
revoke all on all functions in schema bifurcate from public;
grant usage on schema bifurcate to billing_commerce_runtime;
grant select on bifurcate.ready_tool_bindings to billing_commerce_runtime;
grant execute on function bifurcate.accept_usage_event(text,text,text,text,text,text,text,numeric,text,timestamptz,timestamptz,timestamptz,jsonb,text,text)
  to billing_commerce_runtime;

commit;
