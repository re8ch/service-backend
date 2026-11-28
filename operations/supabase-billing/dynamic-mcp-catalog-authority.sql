-- Apply to re8ch_billing through the reviewed postgres_admin_sql workflow only.
-- pg-identity remains authoritative for subjects, organizations and membership.
-- These domains preserve canonical references; they do not mirror identity rows.
begin transaction isolation level serializable;

set local role re8ch_billing_migrator;
create schema if not exists bifurcate authorization re8ch_billing_owner;
create schema if not exists bifurcate_api authorization re8ch_billing_owner;

do $$ begin
  create domain bifurcate.identity_subject_ref as uuid;
exception when duplicate_object then null; end $$;
do $$ begin
  create domain bifurcate.identity_organization_ref as text
    check (value <> '*' and value ~ '^[a-z0-9][a-z0-9_-]{0,127}$');
exception when duplicate_object then null; end $$;

create table if not exists bifurcate.mcp_servers (
  server_ref text primary key check (server_ref ~ '^mcpsrv_[a-f0-9]{32}$'),
  owner_org_ref bifurcate.identity_organization_ref not null,
  slug text not null check (slug ~ '^[a-z][a-z0-9-]{0,62}$'),
  display_name text not null check (length(display_name) between 1 and 200),
  service_instance_ref text not null check (length(service_instance_ref) between 1 and 512),
  lifecycle text not null default 'draft'
    check (lifecycle in ('draft','active','suspended','retired')),
  created_by_subject bifurcate.identity_subject_ref not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_org_ref, slug),
  unique (server_ref, owner_org_ref)
);

create table if not exists bifurcate.mcp_tools (
  tool_ref text primary key check (tool_ref ~ '^mcptool_[a-f0-9]{32}$'),
  server_ref text not null,
  owner_org_ref bifurcate.identity_organization_ref not null,
  tool_name text not null check (tool_name ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  ownership text not null default 'organization'
    check (ownership in ('organization','platform')),
  lifecycle text not null default 'active'
    check (lifecycle in ('active','suspended','retired')),
  created_by_subject bifurcate.identity_subject_ref not null,
  created_at timestamptz not null default now(),
  foreign key (server_ref, owner_org_ref)
    references bifurcate.mcp_servers(server_ref, owner_org_ref),
  unique (server_ref, tool_name),
  unique (server_ref, tool_ref),
  unique (tool_ref, owner_org_ref)
);

create table if not exists bifurcate.mcp_server_revisions (
  revision_ref text primary key check (revision_ref ~ '^mcprev_[a-f0-9]{32}$'),
  server_ref text not null,
  owner_org_ref bifurcate.identity_organization_ref not null,
  revision_number bigint not null check (revision_number > 0),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  definition_sha256 text not null check (definition_sha256 ~ '^[a-f0-9]{64}$'),
  state text not null default 'draft' check (state in ('draft','sealed','retired')),
  created_by_subject bifurcate.identity_subject_ref not null,
  created_at timestamptz not null default now(),
  sealed_at timestamptz,
  foreign key (server_ref, owner_org_ref)
    references bifurcate.mcp_servers(server_ref, owner_org_ref),
  unique (server_ref, revision_number),
  unique (server_ref, revision_ref),
  unique (revision_ref, owner_org_ref),
  unique (revision_ref, server_ref, owner_org_ref),
  check ((state <> 'sealed') or sealed_at is not null)
);

create table if not exists bifurcate.mcp_revision_tools (
  revision_ref text not null,
  server_ref text not null,
  owner_org_ref bifurcate.identity_organization_ref not null,
  tool_ref text not null,
  ordinal integer not null check (ordinal >= 0),
  input_schema jsonb not null check (jsonb_typeof(input_schema) = 'object'),
  tool_definition jsonb not null check (jsonb_typeof(tool_definition) = 'object'),
  schema_sha256 text not null check (schema_sha256 ~ '^[a-f0-9]{64}$'),
  primary key (revision_ref, tool_ref),
  foreign key (revision_ref, server_ref, owner_org_ref)
    references bifurcate.mcp_server_revisions(revision_ref, server_ref, owner_org_ref),
  foreign key (server_ref, tool_ref)
    references bifurcate.mcp_tools(server_ref, tool_ref),
  foreign key (tool_ref, owner_org_ref)
    references bifurcate.mcp_tools(tool_ref, owner_org_ref),
  unique (revision_ref, ordinal),
  unique (revision_ref, tool_ref, schema_sha256)
);

create table if not exists bifurcate.mcp_active_revisions (
  server_ref text primary key,
  owner_org_ref bifurcate.identity_organization_ref not null,
  revision_ref text not null,
  activated_by_subject bifurcate.identity_subject_ref not null,
  activation_sequence bigint not null check (activation_sequence > 0),
  activated_at timestamptz not null default now(),
  foreign key (server_ref, owner_org_ref)
    references bifurcate.mcp_servers(server_ref, owner_org_ref),
  foreign key (revision_ref, server_ref, owner_org_ref)
    references bifurcate.mcp_server_revisions(revision_ref, server_ref, owner_org_ref),
  unique (server_ref, activation_sequence)
);

create table if not exists bifurcate.mcp_catalog_registrations (
  registration_ref text primary key check (registration_ref ~ '^mcpreg_[a-f0-9]{32}$'),
  owner_org_ref bifurcate.identity_organization_ref not null,
  server_ref text not null,
  revision_ref text not null,
  requested_by_subject bifurcate.identity_subject_ref not null,
  oauth_client_ref text not null check (oauth_client_ref ~ '^oauthcl_[A-Za-z0-9_-]{16,200}$'),
  operation_key text not null check (length(operation_key) between 1 and 200),
  request_sha256 text not null check (request_sha256 ~ '^[a-f0-9]{64}$'),
  state text not null default 'requested'
    check (state in ('requested','validated','approved','published','rejected','revoked')),
  reviewed_by_subject bifurcate.identity_subject_ref,
  review_reason text check (review_reason is null or length(review_reason) between 1 and 500),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz,
  revoked_at timestamptz,
  foreign key (server_ref, owner_org_ref)
    references bifurcate.mcp_servers(server_ref, owner_org_ref),
  foreign key (revision_ref, server_ref, owner_org_ref)
    references bifurcate.mcp_server_revisions(revision_ref, server_ref, owner_org_ref),
  unique (owner_org_ref, operation_key),
  check ((state not in ('approved','published')) or
    (reviewed_by_subject is not null and reviewed_by_subject <> requested_by_subject)),
  check ((state <> 'published') or published_at is not null),
  check ((state <> 'revoked') or revoked_at is not null),
  check ((state <> 'rejected') or review_reason is not null)
);

create table if not exists bifurcate.mcp_catalog_outbox (
  event_id bigint generated always as identity primary key,
  aggregate_kind text not null check (aggregate_kind in ('server','revision','registration')),
  aggregate_ref text not null check (length(aggregate_ref) between 1 and 200),
  event_type text not null check (event_type ~ '^[a-z][a-z0-9_.-]{2,127}$'),
  event_version integer not null default 1 check (event_version = 1),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  deduplication_key text not null check (length(deduplication_key) between 1 and 300),
  created_at timestamptz not null default now(),
  published_at timestamptz,
  publish_attempts integer not null default 0 check (publish_attempts >= 0),
  last_error text,
  unique (deduplication_key)
);
create index if not exists mcp_catalog_outbox_pending_idx
  on bifurcate.mcp_catalog_outbox(event_id) where published_at is null;

create or replace function bifurcate.reject_mcp_revision_payload_mutation() returns trigger
language plpgsql set search_path = pg_catalog, pg_temp as $$
begin
  if old.server_ref is distinct from new.server_ref
     or old.owner_org_ref is distinct from new.owner_org_ref
     or old.revision_number is distinct from new.revision_number
     or old.definition is distinct from new.definition
     or old.definition_sha256 is distinct from new.definition_sha256
     or old.created_by_subject is distinct from new.created_by_subject then
    raise exception 'MCP server revision payloads are immutable' using errcode = '55000';
  end if;
  if old.state <> 'draft' and old.state is distinct from new.state then
    raise exception 'sealed MCP server revision state is immutable' using errcode = '55000';
  end if;
  return new;
end;
$$;
drop trigger if exists mcp_server_revision_payload_immutable on bifurcate.mcp_server_revisions;
create trigger mcp_server_revision_payload_immutable before update on bifurcate.mcp_server_revisions
for each row execute function bifurcate.reject_mcp_revision_payload_mutation();

create or replace function bifurcate.reject_sealed_mcp_revision_tool_mutation() returns trigger
language plpgsql set search_path = bifurcate, pg_catalog, pg_temp as $$
declare v_revision text;
begin
  v_revision := coalesce(new.revision_ref, old.revision_ref);
  if exists (select 1 from mcp_server_revisions where revision_ref = v_revision and state <> 'draft') then
    raise exception 'sealed MCP revision tools are immutable' using errcode = '55000';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end;
$$;
drop trigger if exists sealed_mcp_revision_tools_immutable on bifurcate.mcp_revision_tools;
create trigger sealed_mcp_revision_tools_immutable before insert or update or delete
on bifurcate.mcp_revision_tools for each row
execute function bifurcate.reject_sealed_mcp_revision_tool_mutation();

create or replace function bifurcate_api.request_mcp_catalog_registration(
  p_registration_ref text, p_owner_org_ref text, p_server_ref text,
  p_revision_ref text, p_requested_by_subject uuid, p_operation_key text,
  p_request_sha256 text, p_oauth_client_ref text
) returns table(registration_ref text, state text, idempotent_replay boolean)
language plpgsql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
declare v_existing bifurcate.mcp_catalog_registrations%rowtype;
begin
  insert into mcp_catalog_registrations(registration_ref, owner_org_ref, server_ref,
    revision_ref, requested_by_subject, operation_key, request_sha256, oauth_client_ref)
  values(p_registration_ref, p_owner_org_ref, p_server_ref, p_revision_ref,
    p_requested_by_subject, p_operation_key, p_request_sha256, p_oauth_client_ref)
  on conflict (owner_org_ref, operation_key) do nothing
  returning * into v_existing;
  if found then
    insert into mcp_catalog_outbox(aggregate_kind, aggregate_ref, event_type, payload,
      deduplication_key)
    values('registration', p_registration_ref, 'catalog.registration.requested',
      jsonb_build_object('registrationRef', p_registration_ref,
        'ownerOrgRef', p_owner_org_ref, 'serverRef', p_server_ref,
        'revisionRef', p_revision_ref, 'requestSha256', p_request_sha256,
        'oauthClientRef', p_oauth_client_ref),
      'catalog.registration.requested:' || p_registration_ref);
    return query select p_registration_ref, 'requested'::text, false;
    return;
  end if;
  select * into strict v_existing from mcp_catalog_registrations
   where owner_org_ref = p_owner_org_ref and operation_key = p_operation_key;
  if v_existing.registration_ref is distinct from p_registration_ref
     or v_existing.server_ref is distinct from p_server_ref
     or v_existing.revision_ref is distinct from p_revision_ref
     or v_existing.requested_by_subject is distinct from p_requested_by_subject
     or v_existing.request_sha256 is distinct from p_request_sha256
     or v_existing.oauth_client_ref is distinct from p_oauth_client_ref then
    raise exception 'operation key already exists with different registration content'
      using errcode = '23505';
  end if;
  return query select v_existing.registration_ref, v_existing.state, true;
end;
$$;

create or replace function bifurcate_api.transition_mcp_catalog_registration(
  p_registration_ref text, p_expected_state text, p_next_state text,
  p_actor_subject uuid, p_reason text default null
) returns table(registration_ref text, state text)
language plpgsql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
declare v_row bifurcate.mcp_catalog_registrations%rowtype;
declare v_allowed boolean := false;
begin
  select * into strict v_row from mcp_catalog_registrations
   where mcp_catalog_registrations.registration_ref = p_registration_ref for update;
  if v_row.state <> p_expected_state then
    raise exception 'registration state changed' using errcode = '40001';
  end if;
  v_allowed := (v_row.state = 'requested' and p_next_state in ('validated','rejected'))
    or (v_row.state = 'validated' and p_next_state in ('approved','rejected'))
    or (v_row.state = 'approved' and p_next_state in ('published','revoked'))
    or (v_row.state = 'published' and p_next_state = 'revoked');
  if not v_allowed then
    raise exception 'invalid registration state transition' using errcode = '22023';
  end if;
  if p_next_state in ('approved','published') and p_actor_subject = v_row.requested_by_subject then
    raise exception 'registration approval requires a different subject' using errcode = '42501';
  end if;
  if p_next_state in ('rejected','revoked') and p_reason is null then
    raise exception 'rejection and revocation require a reason' using errcode = '22023';
  end if;
  if p_next_state = 'published' and not exists (
    select 1 from mcp_server_revisions r where r.revision_ref = v_row.revision_ref
      and r.server_ref = v_row.server_ref and r.owner_org_ref = v_row.owner_org_ref
      and r.state = 'sealed') then
    raise exception 'only a sealed revision can be published' using errcode = '55000';
  end if;
  update mcp_catalog_registrations set state = p_next_state,
    reviewed_by_subject = case when p_next_state in ('approved','published','rejected')
      then p_actor_subject else reviewed_by_subject end,
    review_reason = case when p_next_state in ('rejected','revoked') then p_reason else review_reason end,
    published_at = case when p_next_state = 'published' then now() else published_at end,
    revoked_at = case when p_next_state = 'revoked' then now() else revoked_at end,
    updated_at = now()
  where mcp_catalog_registrations.registration_ref = p_registration_ref;
  insert into mcp_catalog_outbox(aggregate_kind, aggregate_ref, event_type, payload,
    deduplication_key)
  values('registration', p_registration_ref, 'catalog.registration.' || p_next_state,
    jsonb_build_object('registrationRef', p_registration_ref, 'ownerOrgRef', v_row.owner_org_ref,
      'serverRef', v_row.server_ref, 'revisionRef', v_row.revision_ref,
      'state', p_next_state),
    'catalog.registration.' || p_next_state || ':' || p_registration_ref);
  return query select p_registration_ref, p_next_state;
end;
$$;

create or replace function bifurcate_api.activate_mcp_catalog_revision(
  p_registration_ref text, p_actor_subject uuid, p_expected_active_revision_ref text default null
) returns table(server_ref text, revision_ref text, activation_sequence bigint)
language plpgsql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
declare v_row bifurcate.mcp_catalog_registrations%rowtype;
declare v_sequence bigint;
declare v_active_revision_ref text;
begin
  select * into strict v_row from mcp_catalog_registrations
   where mcp_catalog_registrations.registration_ref = p_registration_ref for update;
  if v_row.state <> 'published' then
    raise exception 'only a published registration can become active' using errcode = '55000';
  end if;
  perform 1 from mcp_servers s where s.server_ref = v_row.server_ref for update;
  select a.revision_ref into v_active_revision_ref from mcp_active_revisions a
    where a.server_ref = v_row.server_ref;
  if v_active_revision_ref is distinct from p_expected_active_revision_ref then
    raise exception 'active MCP revision changed' using errcode = '40001';
  end if;
  select coalesce(max(a.activation_sequence), 0) + 1 into v_sequence
    from mcp_active_revisions a where a.server_ref = v_row.server_ref;
  insert into mcp_active_revisions(server_ref, owner_org_ref, revision_ref,
    activated_by_subject, activation_sequence)
  values(v_row.server_ref, v_row.owner_org_ref, v_row.revision_ref,
    p_actor_subject, v_sequence)
  on conflict on constraint mcp_active_revisions_pkey do update set
    revision_ref = excluded.revision_ref,
    activated_by_subject = excluded.activated_by_subject,
    activation_sequence = excluded.activation_sequence,
    activated_at = now();
  insert into mcp_catalog_outbox(aggregate_kind, aggregate_ref, event_type, payload,
    deduplication_key)
  values('revision', v_row.revision_ref, 'catalog.revision.activated',
    jsonb_build_object('serverRef', v_row.server_ref, 'ownerOrgRef', v_row.owner_org_ref,
      'revisionRef', v_row.revision_ref, 'activationSequence', v_sequence),
    'catalog.revision.activated:' || v_row.server_ref || ':' || v_sequence);
  return query select v_row.server_ref, v_row.revision_ref, v_sequence;
end;
$$;

create or replace view bifurcate.active_mcp_catalog as
select s.server_ref, s.owner_org_ref, s.slug, s.display_name, s.service_instance_ref,
       a.revision_ref, a.activation_sequence, r.definition_sha256,
       t.tool_ref, t.tool_name, rt.schema_sha256, rt.input_schema, rt.tool_definition,
       r.definition as revision_definition
from bifurcate.mcp_active_revisions a
join bifurcate.mcp_servers s using (server_ref, owner_org_ref)
join bifurcate.mcp_server_revisions r using (revision_ref, server_ref, owner_org_ref)
join bifurcate.mcp_revision_tools rt using (revision_ref, server_ref, owner_org_ref)
join bifurcate.mcp_tools t using (tool_ref, server_ref, owner_org_ref)
where s.lifecycle = 'active' and r.state = 'sealed' and t.lifecycle = 'active';

create or replace function bifurcate_api.claim_mcp_catalog_projection_events(p_limit integer default 100)
returns table(event_id bigint, event_type text, payload jsonb)
language sql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
  with claimed as (
    select o.event_id from mcp_catalog_outbox o
    where o.published_at is null
    order by o.event_id
    limit greatest(1, least(p_limit, 256))
    for update skip locked
  ), updated as (
    update mcp_catalog_outbox o set publish_attempts = publish_attempts + 1
    from claimed c where o.event_id = c.event_id
    returning o.event_id, o.event_type, o.payload
  ) select * from updated order by event_id;
$$;

create or replace function bifurcate_api.ack_mcp_catalog_projection_event(p_event_id bigint)
returns boolean language sql security definer
set search_path = bifurcate, pg_catalog, pg_temp
as $$
  with updated as (
    update mcp_catalog_outbox set published_at = coalesce(published_at, now()), last_error = null
    where event_id = p_event_id returning 1
  ) select exists(select 1 from updated);
$$;

revoke all on schema bifurcate from public;
revoke all on all tables in schema bifurcate from public;
revoke all on all functions in schema bifurcate from public;
revoke all on schema bifurcate_api from public;
revoke all on all functions in schema bifurcate_api from public;
grant usage on schema bifurcate to billing_commerce_runtime;
grant select on bifurcate.active_mcp_catalog to billing_commerce_runtime;
grant usage on schema bifurcate_api to billing_commerce_runtime;
grant execute on function bifurcate_api.request_mcp_catalog_registration(text,text,text,text,uuid,text,text,text)
  to billing_commerce_runtime;
grant execute on function bifurcate_api.transition_mcp_catalog_registration(text,text,text,uuid,text)
  to billing_commerce_runtime;
grant execute on function bifurcate_api.activate_mcp_catalog_revision(text,uuid,text)
  to billing_commerce_runtime;
grant execute on function bifurcate_api.claim_mcp_catalog_projection_events(integer)
  to billing_commerce_runtime;
grant execute on function bifurcate_api.ack_mcp_catalog_projection_event(bigint)
  to billing_commerce_runtime;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'billing_supabase_pgrst_rw') then
    grant billing_commerce_runtime to billing_supabase_pgrst_rw;
  end if;
  if exists (select 1 from pg_roles where rolname = 'billing_supabase_pgrst_ro') then
    grant billing_commerce_runtime to billing_supabase_pgrst_ro;
  end if;
end
$$;
notify pgrst, 'reload schema';

commit;
