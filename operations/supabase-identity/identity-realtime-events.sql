-- Run as postgres against re8ch_identity only after Realtime has created
-- realtime.messages and realtime.send(). Events are cache invalidations, never
-- a source of authorization or a transport for profile data.
begin;

drop policy if exists identity_user_receive on realtime.messages;
create policy identity_user_receive on realtime.messages
  for select to authenticated
  using (
    realtime.topic() = 'identity:user:' ||
      (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')
  );

create or replace function core.identity_notify_user(
  p_user_id uuid, p_kind text, p_tenant_ref text default null
) returns void
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if p_user_id is not null then
    perform realtime.send(
      jsonb_build_object('kind', p_kind, 'tenant_ref', p_tenant_ref,
                         'changed_at', clock_timestamp()),
      'identity_changed', 'identity:user:' || p_user_id::text, true
    );
  end if;
end;
$$;
revoke all on function core.identity_notify_user(uuid, text, text) from public;

create or replace function core.identity_profile_changed() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  perform core.identity_notify_user(new.id, 'profile', null);
  return new;
end;
$$;
drop trigger if exists identity_profile_changed on auth.users;
create trigger identity_profile_changed
  after update of email, raw_user_meta_data on auth.users
  for each row when (
    old.email is distinct from new.email or
    old.raw_user_meta_data is distinct from new.raw_user_meta_data
  ) execute function core.identity_profile_changed();

create or replace function core.identity_member_changed() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if tg_op <> 'INSERT' then
    perform core.identity_notify_user(old.user_id, 'membership', old.tenant_ref);
  end if;
  if tg_op <> 'DELETE' then
    perform core.identity_notify_user(new.user_id, 'membership', new.tenant_ref);
  end if;
  return null;
end;
$$;
drop trigger if exists identity_member_changed on core.organization_members;
create trigger identity_member_changed
  after insert or update or delete on core.organization_members
  for each row execute function core.identity_member_changed();

create or replace function core.identity_group_member_changed() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare v_tenant_ref text;
begin
  if tg_op <> 'INSERT' then
    select tenant_ref into v_tenant_ref from core.groups where id = old.group_id;
    perform core.identity_notify_user(old.user_id, 'permissions', v_tenant_ref);
  end if;
  if tg_op <> 'DELETE' then
    select tenant_ref into v_tenant_ref from core.groups where id = new.group_id;
    perform core.identity_notify_user(new.user_id, 'permissions', v_tenant_ref);
  end if;
  return null;
end;
$$;
drop trigger if exists identity_group_member_changed on core.group_members;
create trigger identity_group_member_changed
  after insert or update or delete on core.group_members
  for each row execute function core.identity_group_member_changed();

create or replace function core.identity_role_binding_changed() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare v_user_id uuid;
begin
  if tg_op <> 'INSERT' then
    perform core.identity_notify_user(old.user_id, 'permissions', old.tenant_ref);
    if old.group_id is not null then
      for v_user_id in select user_id from core.group_members
                       where group_id = old.group_id loop
        perform core.identity_notify_user(v_user_id, 'permissions', old.tenant_ref);
      end loop;
    end if;
  end if;
  if tg_op <> 'DELETE' then
    perform core.identity_notify_user(new.user_id, 'permissions', new.tenant_ref);
    if new.group_id is not null then
      for v_user_id in select user_id from core.group_members
                       where group_id = new.group_id loop
        perform core.identity_notify_user(v_user_id, 'permissions', new.tenant_ref);
      end loop;
    end if;
  end if;
  return null;
end;
$$;
drop trigger if exists identity_role_binding_changed on core.role_bindings;
create trigger identity_role_binding_changed
  after insert or update or delete on core.role_bindings
  for each row execute function core.identity_role_binding_changed();

create or replace function core.identity_organization_changed() returns trigger
language plpgsql security definer
set search_path = pg_catalog, pg_temp
as $$
declare v_user_id uuid;
begin
  for v_user_id in select user_id from core.organization_members
                   where tenant_ref = new.tenant_ref loop
    perform core.identity_notify_user(v_user_id, 'organization', new.tenant_ref);
  end loop;
  return new;
end;
$$;
drop trigger if exists identity_organization_changed on core.organizations;
create trigger identity_organization_changed
  after update of display_name, status on core.organizations
  for each row when (
    old.display_name is distinct from new.display_name or
    old.status is distinct from new.status
  ) execute function core.identity_organization_changed();

commit;
