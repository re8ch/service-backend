create schema if not exists dataspace;
create table if not exists dataspace.workspaces (
  id uuid primary key, display_name text not null, root_item_id uuid not null unique,
  created_at timestamptz not null default now()
);
create table if not exists dataspace.items (
  id uuid primary key, workspace_id uuid not null references dataspace.workspaces(id),
  parent_id uuid references dataspace.items(id), name text not null,
  kind text not null check (kind in ('file','folder')), storage_key text,
  content_type text, size bigint not null default 0, version bigint not null default 1,
  etag text not null, metadata jsonb not null default '{}', deleted_at timestamptz,
  modified_at timestamptz not null default now(), unique nulls not distinct(workspace_id,parent_id,name)
);
create table if not exists dataspace.changes (
  cursor bigint generated always as identity primary key, workspace_id uuid not null,
  item_id uuid not null, event_type text not null, item_version bigint not null,
  payload jsonb not null, created_at timestamptz not null default now()
);
create index if not exists items_parent_idx on dataspace.items(workspace_id,parent_id) where deleted_at is null;
create index if not exists items_search_idx on dataspace.items using gin(to_tsvector('simple',name));
create index if not exists changes_workspace_idx on dataspace.changes(workspace_id,cursor);
create table if not exists dataspace.shares (id uuid primary key, workspace_id uuid not null, subject text not null, role text not null, expires_at timestamptz);
create table if not exists dataspace.devices (id uuid primary key, subject text not null, token_hash text not null, last_seen_at timestamptz);

