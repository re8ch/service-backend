-- Apply to re8ch_billing through the reviewed postgres_admin_sql workflow only.
-- Runtime credentials remain server-held; this migration contains no secrets.

begin transaction isolation level serializable;

set local role re8ch_billing_migrator;

create schema if not exists tenant_runtime authorization re8ch_billing_owner;
revoke all on schema tenant_runtime from public;

create table if not exists tenant_runtime.dynamic_skills (
  ref text primary key, tenant text not null, name text not null, version integer not null,
  description text not null, workflow text not null,
  status text not null check (status in ('Pending','Published','Revoked')),
  subject text not null, reviewer text, created_at text not null, reviewed_at text,
  owner_subject text not null default '', unique(tenant,name,version));
create index if not exists dynamic_skills_visible on tenant_runtime.dynamic_skills(tenant,status,name,version);

create table if not exists tenant_runtime.collaboration_connections (
  ref text primary key, tenant text not null, subject text not null,
  request_hash text not null, scopes text not null, state text not null,
  account_id text not null default '', expires bigint not null default 0,
  verification_uri text not null default '', user_code text not null default '',
  next_poll bigint not null default 0, poll_interval bigint not null default 5);
create index if not exists collaboration_owner on tenant_runtime.collaboration_connections(tenant,subject);
create table if not exists tenant_runtime.collaboration_writes (
  tenant text not null, subject text not null, key text not null,
  request_hash text not null, state text not null, result text not null default '',
  primary key(tenant,subject,key));

create table if not exists tenant_runtime.instances (
  resource_ref text primary key, tenant_ref text not null, name text not null,
  service_class text not null, state text not null, spec_json text not null,
  generation bigint not null, created_at text not null, updated_at text not null,
  deleted_at text, unique(tenant_ref,name));
create table if not exists tenant_runtime.operations (
  operation_id text primary key, tenant_ref text not null, resource_ref text not null,
  action text not null, idempotency_key text not null, request_hash text not null,
  before_json text, after_json text, result_json text not null, created_at text not null,
  unique(tenant_ref,idempotency_key));
create table if not exists tenant_runtime.bindings (
  binding_ref text primary key, tenant_ref text not null, resource_ref text not null,
  binding_type text not null, binding_json text not null, revoked_at text, created_at text not null);
create table if not exists tenant_runtime.received_usage (
  event_id text primary key, tenant_ref text not null, resource_ref text not null,
  event_json text not null, received_at text not null);
create index if not exists received_usage_tenant_resource on tenant_runtime.received_usage(tenant_ref,resource_ref);
create index if not exists received_usage_tenant_received on tenant_runtime.received_usage(tenant_ref,received_at);
create table if not exists tenant_runtime.usage_outbox (
  event_id text primary key, tenant_ref text not null, resource_ref text not null,
  event_json text not null, delivered_at text, created_at text not null);
create index if not exists usage_outbox_tenant_resource on tenant_runtime.usage_outbox(tenant_ref,resource_ref);
create table if not exists tenant_runtime.cluster_registrations (
  registration_ref text primary key, tenant_ref text not null, cluster_uid text not null,
  region_ref text not null, issuer text not null, public_key_hash text not null,
  capabilities_json text not null, state text not null, enrollment_ref text not null,
  created_at text not null, updated_at text not null, unique(tenant_ref,cluster_uid));
create table if not exists tenant_runtime.cluster_connections (
  connection_ref text primary key, registration_ref text not null unique,
  tenant_ref text not null, state text not null, identity_ref text not null,
  last_heartbeat text, revoked_at text, created_at text not null);
create table if not exists tenant_runtime.admin_database_operations (
  organization_ref text not null, idempotency_key text not null, request_hash text not null,
  state text not null check(state in ('pending','completed')), result_json text,
  created_at text not null, updated_at text not null,
  primary key(organization_ref,idempotency_key));

create table if not exists tenant_runtime.mcp_catalog_revisions (
  revision text primary key, tenant_ref text not null, service_name text not null,
  definition_json text not null, state text not null check(state in ('active','paused')),
  previous_revision text, operation_key text not null, actor text not null, created_at text not null,
  unique(tenant_ref,operation_key));
create index if not exists mcp_catalog_current on tenant_runtime.mcp_catalog_revisions(tenant_ref,service_name,created_at);
create table if not exists tenant_runtime.mcp_catalog_heads (
  tenant_ref text not null, service_name text not null, revision text not null references tenant_runtime.mcp_catalog_revisions(revision),
  primary key(tenant_ref,service_name));
create table if not exists tenant_runtime.mcp_catalog_outbox (
  sequence bigint generated always as identity primary key, event_id text not null unique,
  tenant_ref text not null, service_name text not null, revision text not null,
  event_json text not null, created_at text not null);
create index if not exists mcp_catalog_outbox_tenant_sequence on tenant_runtime.mcp_catalog_outbox(tenant_ref,sequence);

create table if not exists tenant_runtime.credential_redemptions (
  token_hash text primary key, credential_ref text not null, tenant text not null,
  expires_at bigint not null, consumed_at bigint);
create table if not exists tenant_runtime.credential_adoptions (
  token_hash text primary key, tenant text not null, expires_at bigint not null, consumed_at bigint);

grant usage on schema tenant_runtime to billing_commerce_runtime;
grant select,insert,update,delete on all tables in schema tenant_runtime to billing_commerce_runtime;
grant usage,select on all sequences in schema tenant_runtime to billing_commerce_runtime;

commit;
