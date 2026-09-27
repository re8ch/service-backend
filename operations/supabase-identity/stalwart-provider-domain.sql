-- Apply as supabase_auth_admin after https://id.re8ch.com serves Stalwart's
-- OIDC discovery document with the new issuer. This preserves the client
-- registration and forces GoTrue to refresh the provider metadata.
begin;

update auth.custom_oauth_providers
set issuer = 'https://id.re8ch.com',
    cached_discovery = null,
    discovery_cached_at = null,
    updated_at = now()
where identifier = 'custom:stalwart'
  and issuer = 'https://id-stalwart.tools.re8ch.com';

do $$
begin
  if not exists (
    select 1 from auth.custom_oauth_providers
    where identifier = 'custom:stalwart'
      and issuer = 'https://id.re8ch.com'
      and enabled
  ) then
    raise exception 'Stalwart custom OAuth provider was not migrated';
  end if;
end
$$;

commit;
