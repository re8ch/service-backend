-- Run against postgres as an approved Pigsty administrator, outside a transaction.
-- Set passwords through the secret manager; this file contains no credentials.
select 'create database re8ch_commerce' where not exists
  (select 1 from pg_database where datname='re8ch_commerce') \gexec

do $$ begin
  if not exists (select 1 from pg_roles where rolname='commerce_anon') then
    create role commerce_anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname='commerce_authenticator') then
    create role commerce_authenticator login noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname='commerce_ro_authenticator') then
    create role commerce_ro_authenticator login noinherit;
  end if;
end $$;
grant connect on database re8ch_commerce to commerce_authenticator,commerce_ro_authenticator;
alter role commerce_ro_authenticator set default_transaction_read_only=on;

-- After cloud-functions/sql/046_re8ch_commerce.sql has been applied to re8ch_commerce:
-- grant commerce_runtime to commerce_authenticator,commerce_ro_authenticator;
-- provision distinct login passwords and identical JWT signing secret out of band.
