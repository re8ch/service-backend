-- Apply to re8ch_billing only after GoTrue, Storage, and Realtime have created
-- their managed schemas. This migration stores no Identity password or session.
begin;

create table if not exists billing.identity_subject_map (
  billing_user_id uuid primary key references auth.users(id) on delete cascade,
  identity_subject uuid not null unique,
  provider text not null check (provider = 'keycloak'),
  linked_at timestamptz not null default now()
);

create or replace function billing.sync_identity_subject_map() returns trigger
language plpgsql security definer
set search_path = billing, pg_catalog, pg_temp
as $$
declare
  v_subject text := new.identity_data ->> 'sub';
begin
  if new.provider <> 'keycloak' then
    raise exception 'billing auth only accepts the Identity OIDC provider';
  end if;
  if v_subject is null or v_subject !~
    '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
    raise exception 'Identity OIDC subject is not a canonical UUID';
  end if;
  insert into billing.identity_subject_map(billing_user_id, identity_subject, provider)
  values(new.user_id, v_subject::uuid, new.provider)
  on conflict (billing_user_id) do update
    set identity_subject = excluded.identity_subject,
        provider = excluded.provider;
  return new;
end;
$$;
revoke all on function billing.sync_identity_subject_map() from public;

drop trigger if exists billing_identity_subject_map on auth.identities;
create trigger billing_identity_subject_map
  after insert or update of identity_data on auth.identities
  for each row execute function billing.sync_identity_subject_map();

insert into storage.buckets(id, name, public)
values ('billing-private', 'billing-private', false)
on conflict (id) do update set public = false;

alter table storage.objects enable row level security;
drop policy if exists billing_storage_owner_select on storage.objects;
create policy billing_storage_owner_select on storage.objects
  for select to authenticated
  using (
    bucket_id = 'billing-private' and owner_id =
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );
drop policy if exists billing_storage_owner_insert on storage.objects;
create policy billing_storage_owner_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'billing-private' and owner_id =
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );
drop policy if exists billing_storage_owner_update on storage.objects;
create policy billing_storage_owner_update on storage.objects
  for update to authenticated
  using (
    bucket_id = 'billing-private' and owner_id =
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  )
  with check (
    bucket_id = 'billing-private' and owner_id =
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );
drop policy if exists billing_storage_owner_delete on storage.objects;
create policy billing_storage_owner_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'billing-private' and owner_id =
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );

drop policy if exists billing_user_receive on realtime.messages;
create policy billing_user_receive on realtime.messages
  for select to authenticated
  using (
    realtime.topic() = 'billing:user:' ||
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );

commit;
