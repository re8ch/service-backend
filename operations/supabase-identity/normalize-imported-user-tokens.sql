-- Run once as supabase_auth_admin on re8ch_identity after importing legacy
-- users. GoTrue v2.196.0 scans these fields into Go strings, so NULL values
-- in otherwise unused tokens prevent a migrated account from signing in.
begin;

update auth.users
set confirmation_token = coalesce(confirmation_token, ''),
    recovery_token = coalesce(recovery_token, ''),
    email_change_token_new = coalesce(email_change_token_new, ''),
    email_change = coalesce(email_change, '')
where confirmation_token is null
   or recovery_token is null
   or email_change_token_new is null
   or email_change is null;

do $$
begin
  if exists (
    select 1 from auth.users
    where confirmation_token is null
       or recovery_token is null
       or email_change_token_new is null
       or email_change is null
  ) then
    raise exception 'GoTrue user token normalization incomplete';
  end if;
end
$$;

commit;
