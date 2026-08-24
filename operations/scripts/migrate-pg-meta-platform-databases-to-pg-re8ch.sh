#!/usr/bin/env bash
set -euo pipefail

# Stage non-Qwen platform databases from pg-meta onto pg-re8ch. This script is
# intentionally create-only: it refuses to overwrite an existing target DB and
# never drops a source DB. Public routing is a separate cutover step.

SOURCE_SSH="${SOURCE_SSH:-aspire}"
TARGET_SSH="${TARGET_SSH:-r640}"

databases=(ai_gateway auth image meta observable)
roles=(
  ai_gateway_admin ai_gateway_app_ro ai_gateway_app_rw ai_gateway_migrator
  ai_gateway_owner ai_gateway_pgrst
  auth_admin auth_app_ro auth_app_rw auth_migrator auth_owner auth_pgrst authentik
  dbuser_meta dbuser_view
  image_admin image_app_ro image_app_rw image_migrator image_owner image_pgrst
  observable_app_ro observable_app_rw observable_migrator observable_pgrst
  web_user
)

role_csv="$(printf "'%s'," "${roles[@]}")"
role_csv="${role_csv%,}"

target_databases="$({
  ssh "$TARGET_SSH" "sudo -iu postgres psql -X -qAt -d postgres -c 'select datname from pg_database where not datistemplate'"
} | sort)"

for database in "${databases[@]}"; do
  if grep -Fxq "$database" <<<"$target_databases"; then
    echo "refusing to overwrite existing target database: $database" >&2
    exit 1
  fi
done

echo "creating missing non-Qwen roles on pg-re8ch"
ssh "$SOURCE_SSH" "sudo -iu postgres psql -X -qAt -d postgres" <<SQL \
  | ssh "$TARGET_SSH" "sudo -iu postgres psql -X -v ON_ERROR_STOP=1 -d postgres"
select format(
  'create role %I with %s %s %s %s %s %s %s connection limit %s%s%s;',
  rolname,
  case when rolsuper then 'superuser' else 'nosuperuser' end,
  case when rolinherit then 'inherit' else 'noinherit' end,
  case when rolcreaterole then 'createrole' else 'nocreaterole' end,
  case when rolcreatedb then 'createdb' else 'nocreatedb' end,
  case when rolcanlogin then 'login' else 'nologin' end,
  case when rolreplication then 'replication' else 'noreplication' end,
  case when rolbypassrls then 'bypassrls' else 'nobypassrls' end,
  rolconnlimit,
  case when rolpassword is null then '' else format(' password %L', rolpassword) end,
  case when rolvaliduntil is null then '' else format(' valid until %L', rolvaliduntil) end
)
from pg_authid
where rolname in ($role_csv)
order by rolname;
SQL

echo "restoring role memberships"
ssh "$SOURCE_SSH" "sudo -iu postgres psql -X -qAt -d postgres" <<SQL \
  | ssh "$TARGET_SSH" "sudo -iu postgres psql -X -v ON_ERROR_STOP=1 -d postgres"
select format(
  'grant %I to %I%s;',
  parent.rolname,
  member.rolname,
  case when membership.admin_option then ' with admin option' else '' end
)
from pg_auth_members membership
join pg_roles parent on parent.oid = membership.roleid
join pg_roles member on member.oid = membership.member
where member.rolname in ($role_csv)
  and exists (select 1 from pg_roles target_role where target_role.rolname = parent.rolname)
order by parent.rolname, member.rolname;
SQL

for database in "${databases[@]}"; do
  echo "staging database $database"
  ssh "$TARGET_SSH" "sudo -iu postgres createdb --template=template0 '$database'"
  # Database ACLs for extension-owned compatibility functions can differ by
  # installed PostGIS patch level. Application role ACLs are reconciled after
  # restore; avoid failing the data restore on those extension ACLs.
  if ! ssh "$SOURCE_SSH" "sudo -iu postgres pg_dump -Fc --no-password --no-acl '$database'" \
    | ssh "$TARGET_SSH" "sudo -iu postgres pg_restore --exit-on-error --clean --if-exists --dbname='$database'"; then
    echo "restore failed; removing newly-created target database $database" >&2
    ssh "$TARGET_SSH" "sudo -iu postgres dropdb --if-exists '$database'"
    exit 1
  fi
  source_owner="$(ssh "$SOURCE_SSH" "sudo -iu postgres psql -X -qAt -d postgres -c \"select pg_get_userbyid(datdba) from pg_database where datname = '$database'\"")"
  ssh "$TARGET_SSH" "sudo -iu postgres psql -X -v ON_ERROR_STOP=1 -d postgres -c \"alter database \\\"$database\\\" owner to \\\"$source_owner\\\"\""
  source_fingerprint="$(ssh "$SOURCE_SSH" "sudo -iu postgres psql -X -qAt -d '$database' -c \"select count(*)||'|'||coalesce(sum(pg_total_relation_size(relid)),0) from pg_stat_user_tables\"")"
  target_fingerprint="$(ssh "$TARGET_SSH" "sudo -iu postgres psql -X -qAt -d '$database' -c \"select count(*)||'|'||coalesce(sum(pg_total_relation_size(relid)),0) from pg_stat_user_tables\"")"
  echo "$database source=$source_fingerprint target=$target_fingerprint"
done

echo "staging complete; source databases and routing remain unchanged"
