-- Apply to re8ch_billing through the reviewed postgres_admin_sql workflow only.
-- Identity remains membership authority; this contract consumes short-lived,
-- audience-limited Identity claims and never accepts an organization parameter.
begin transaction isolation level serializable;
set local role re8ch_billing_migrator;

create table if not exists billing.org_cluster_bindings (
  cluster_ref text primary key check (cluster_ref ~ '^[a-z0-9][a-z0-9._:/-]{2,199}$'),
  org_ref text not null check (org_ref ~ '^[a-z0-9][a-z0-9._-]{1,127}$'),
  binding_role text not null check (binding_role in ('owner','operator','viewer')),
  lifecycle text not null check (lifecycle in ('Ready','revoked')),
  binding_digest text not null check (binding_digest ~ '^sha256:[0-9a-f]{64}$'),
  operation_key text not null check (length(operation_key) between 1 and 200),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  created_by_subject uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique (org_ref, operation_key),
  check ((lifecycle = 'Ready' and revoked_at is null) or
         (lifecycle = 'revoked' and revoked_at is not null))
);
create index if not exists org_cluster_bindings_org_idx
  on billing.org_cluster_bindings(org_ref, lifecycle, cluster_ref);
alter table billing.org_cluster_bindings enable row level security;
revoke all on billing.org_cluster_bindings from public, authenticated;

create or replace function billing.current_identity_org_context()
returns table(subject_id uuid, org_ref text, membership_role text)
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare v_claims jsonb; v_subject text;
begin
  v_claims := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
  v_subject := v_claims ->> 'sub';
  if v_subject is null or v_subject !~
    '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
    raise exception 'Identity subject claim is not a canonical UUID' using errcode='28000';
  end if;
  if coalesce(v_claims ->> 'org_membership_state','') <> 'active' then
    raise exception 'active organization membership required' using errcode='42501';
  end if;
  if coalesce(v_claims ->> 'org_ref','') !~ '^[a-z0-9][a-z0-9._-]{1,127}$' then
    raise exception 'active organization claim required' using errcode='28000';
  end if;
  if coalesce(v_claims ->> 'org_role','') not in ('owner','admin','member','viewer') then
    raise exception 'recognized organization role required' using errcode='42501';
  end if;
  return query select v_subject::uuid, v_claims ->> 'org_ref', v_claims ->> 'org_role';
end $$;
revoke all on function billing.current_identity_org_context() from public;

create or replace function commerce_api.register_active_org_cluster_binding(
  p_cluster_ref text, p_binding_role text, p_binding_digest text, p_operation_key text
) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare v_subject uuid; v_org text; v_org_role text; v_hash text; v_claims jsonb;
  v_existing billing.org_cluster_bindings;
begin
  select subject_id,org_ref,membership_role into v_subject,v_org,v_org_role
    from billing.current_identity_org_context();
  if v_org_role not in ('owner','admin') then
    raise exception 'organization owner or admin required' using errcode='42501';
  end if;
  if p_cluster_ref is null or p_cluster_ref !~ '^[a-z0-9][a-z0-9._:/-]{2,199}$' then raise exception 'invalid cluster reference'; end if;
  if p_binding_role not in ('owner','operator','viewer') then raise exception 'invalid cluster binding role'; end if;
  if p_binding_digest is null or p_binding_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'invalid binding digest'; end if;
  if nullif(btrim(p_operation_key),'') is null or length(p_operation_key)>200 then raise exception 'operation key is required'; end if;
  v_claims := nullif(current_setting('request.jwt.claims',true),'')::jsonb;
  if coalesce(v_claims ->> 'cluster_binding_action','') <> 'register'
     or coalesce(v_claims ->> 'cluster_ref','') <> p_cluster_ref
     or coalesce(v_claims ->> 'cluster_binding_digest','') <> p_binding_digest then
    raise exception 'provider-attested cluster binding claims required' using errcode='42501';
  end if;
  v_hash := encode(public.digest(convert_to(jsonb_build_object(
    'clusterRef',p_cluster_ref,'bindingRole',p_binding_role,'bindingDigest',p_binding_digest
  )::text,'UTF8'),'sha256'),'hex');
  perform pg_advisory_xact_lock(hashtextextended('org-cluster-binding:'||v_org||':'||p_operation_key,0));
  select * into v_existing from billing.org_cluster_bindings
    where org_ref=v_org and operation_key=p_operation_key;
  if found then
    if v_existing.request_hash<>v_hash then raise exception 'operation key is bound to a different cluster binding request' using errcode='22023'; end if;
    return jsonb_build_object('clusterRef',v_existing.cluster_ref,'orgRef',v_existing.org_ref,
      'bindingRole',v_existing.binding_role,'lifecycle',v_existing.lifecycle,
      'bindingDigest',v_existing.binding_digest,'idempotentReplay',true);
  end if;
  if exists(select 1 from billing.org_cluster_bindings where cluster_ref=p_cluster_ref) then
    raise exception 'cluster reference is already bound' using errcode='23505';
  end if;
  insert into billing.org_cluster_bindings(cluster_ref,org_ref,binding_role,lifecycle,
    binding_digest,operation_key,request_hash,created_by_subject)
  values(p_cluster_ref,v_org,p_binding_role,'Ready',p_binding_digest,p_operation_key,v_hash,v_subject);
  return jsonb_build_object('clusterRef',p_cluster_ref,'orgRef',v_org,'bindingRole',p_binding_role,
    'lifecycle','Ready','bindingDigest',p_binding_digest,'idempotentReplay',false);
end $$;

create or replace function commerce_api.revoke_active_org_cluster_binding(
  p_cluster_ref text, p_expected_binding_digest text
) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare v_org text; v_org_role text;
begin
  select org_ref,membership_role into v_org,v_org_role from billing.current_identity_org_context();
  if v_org_role not in ('owner','admin') then raise exception 'organization owner or admin required' using errcode='42501'; end if;
  update billing.org_cluster_bindings set lifecycle='revoked',revoked_at=now(),updated_at=now()
    where cluster_ref=p_cluster_ref and org_ref=v_org and lifecycle='Ready'
      and binding_digest=p_expected_binding_digest;
  if not found then raise exception 'Ready cluster binding with expected digest not found' using errcode='P0002'; end if;
  return jsonb_build_object('clusterRef',p_cluster_ref,'orgRef',v_org,'lifecycle','revoked');
end $$;

create or replace function commerce_api.verify_active_org_cluster_binding(
  p_cluster_ref text, p_required_role text default 'viewer'
) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare v_org text; v_membership_role text; v_binding billing.org_cluster_bindings;
  v_membership_rank integer; v_binding_rank integer; v_required_rank integer;
begin
  select org_ref,membership_role into v_org,v_membership_role
    from billing.current_identity_org_context();
  if p_required_role not in ('owner','operator','viewer') then raise exception 'invalid required cluster role'; end if;
  select * into v_binding from billing.org_cluster_bindings
    where cluster_ref=p_cluster_ref and org_ref=v_org and lifecycle='Ready';
  if not found then raise exception 'active organization has no Ready cluster binding' using errcode='42501'; end if;
  v_membership_rank := case v_membership_role
    when 'owner' then 3 when 'admin' then 3 when 'member' then 2 else 1 end;
  v_binding_rank := case v_binding.binding_role
    when 'owner' then 3 when 'operator' then 2 else 1 end;
  v_required_rank := case p_required_role
    when 'owner' then 3 when 'operator' then 2 else 1 end;
  if least(v_membership_rank,v_binding_rank) < v_required_rank then
    raise exception 'cluster binding role is insufficient' using errcode='42501';
  end if;
  return jsonb_build_object('clusterRef',v_binding.cluster_ref,'orgRef',v_binding.org_ref,
    'bindingRole',v_binding.binding_role,'lifecycle',v_binding.lifecycle,
    'effectiveRole',case least(v_membership_rank,v_binding_rank)
      when 3 then 'owner' when 2 then 'operator' else 'viewer' end,
    'bindingDigest',v_binding.binding_digest);
end $$;

revoke all on function commerce_api.register_active_org_cluster_binding(text,text,text,text) from public;
revoke all on function commerce_api.revoke_active_org_cluster_binding(text,text) from public;
revoke all on function commerce_api.verify_active_org_cluster_binding(text,text) from public;
grant execute on function commerce_api.register_active_org_cluster_binding(text,text,text,text),
  commerce_api.revoke_active_org_cluster_binding(text,text),
  commerce_api.verify_active_org_cluster_binding(text,text) to authenticated;
notify pgrst,'reload schema';
commit;
