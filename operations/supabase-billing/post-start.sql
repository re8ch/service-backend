-- Apply to re8ch_billing only after Storage and Realtime have created their
-- managed schemas. Identity remains the sole user authority: Billing stores
-- the canonical Identity UUID directly in business rows and never mirrors
-- users, passwords, sessions, profiles, or organization membership.
begin;

create or replace function billing.current_identity_subject() returns uuid
language plpgsql stable
set search_path = pg_catalog, pg_temp
as $$
declare
  v_claims jsonb;
  v_subject text;
begin
  v_claims := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
  v_subject := v_claims ->> 'sub';
  if v_subject is null or v_subject !~
    '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
    raise exception 'Identity subject claim is not a canonical UUID'
      using errcode = '28000';
  end if;
  return v_subject::uuid;
end;
$$;
revoke all on function billing.current_identity_subject() from public;
grant execute on function billing.current_identity_subject() to authenticated;

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
