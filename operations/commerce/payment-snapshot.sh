#!/usr/bin/env bash
set -euo pipefail
umask 077

# A DBA runs this only after source payment writes are frozen. PGSERVICEFILE and
# PGPASSFILE supply connection details; never pass a password as an argument.
usage() {
  printf 'usage: %s export SOURCE_SERVICE NEW_PRIVATE_DIRECTORY | restore TARGET_SERVICE PRIVATE_DIRECTORY\n' "$0" >&2
  exit 2
}

[[ $# == 3 ]] || usage
action=$1
service=$2
directory=$3
[[ $service =~ ^[A-Za-z0-9_-]+$ ]] || usage
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
connection="service=$service"
for tool in psql pg_dump pg_restore; do command -v "$tool" >/dev/null || exit 2; done
if command -v sha256sum >/dev/null; then
  checksum=(sha256sum)
elif command -v shasum >/dev/null; then
  checksum=(shasum -a 256)
else
  printf 'sha256sum or shasum is required\n' >&2
  exit 2
fi

table_names() {
  psql -X -v ON_ERROR_STOP=1 -At "$connection" -c \
    "select string_agg(relname,',' order by relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='payment' and c.relkind in ('r','p')"
}
summary() {
  psql -X -v ON_ERROR_STOP=1 -At "$connection" -f "$script_dir/payment-snapshot-summary.sql"
}
require_integrity() {
  local report=$1 field
  for field in event_chain_breaks event_hash_mismatches audit_delivery_missing \
    audit_delivery_orphans audit_delivery_hash_mismatches attempt_orphans \
    event_orphans refund_orphans outbox_orphans audit_base_orphans audit_member_orphans \
    pending_credit_outbox; do
    [[ $report == *\"$field\":\ 0* ]] || {
      printf 'payment snapshot integrity failed: %s\n' "$field" >&2
      exit 1
    }
  done
}

if [[ $action == export ]]; then
  [[ ! -e $directory ]] || { printf 'output path already exists\n' >&2; exit 2; }
  server_major=$(psql -X -v ON_ERROR_STOP=1 -At "$connection" -c 'show server_version_num')
  server_major=$((server_major / 10000))
  dump_major=$(pg_dump --version | sed -E 's/.* ([0-9]+)\..*/\1/')
  [[ $dump_major -ge $server_major ]] || {
    printf 'pg_dump major %s is older than source PostgreSQL %s\n' "$dump_major" "$server_major" >&2
    exit 2
  }
  mkdir -m 700 -- "$directory"
  table_names > "$directory/tables.txt"
  summary > "$directory/source-summary.txt"
  require_integrity "$(cat "$directory/source-summary.txt")"
  pg_dump --format=custom --data-only --schema=payment --no-owner --no-acl \
    --dbname="$connection" --file="$directory/payment.dump"
  [[ $(summary) == "$(cat "$directory/source-summary.txt")" ]] || {
    printf 'source changed during snapshot; discard dump and freeze writes\n' >&2; exit 1;
  }
  (cd -- "$directory" && "${checksum[@]}" payment.dump > payment.dump.sha256)
  printf 'payment snapshot exported to %s\n' "$directory"
elif [[ $action == restore ]]; then
  [[ -f $directory/payment.dump && -f $directory/payment.dump.sha256 &&
     -f $directory/tables.txt && -f $directory/source-summary.txt ]] || usage
  (cd -- "$directory" && "${checksum[@]}" -c payment.dump.sha256)
  [[ $(table_names) == "$(cat "$directory/tables.txt")" ]] || {
    printf 'source and target payment tables differ\n' >&2; exit 1;
  }
  psql -X -v ON_ERROR_STOP=1 "$connection" -f "$script_dir/payment-target-empty.sql"
  # DBA privileges are required to disable user and FK triggers. pg_restore
  # re-enables them in the same transaction; a failed restore rolls back.
  pg_restore --data-only --disable-triggers --single-transaction --exit-on-error \
    --no-owner --no-acl --dbname="$connection" "$directory/payment.dump"
  restored_summary=$(summary)
  require_integrity "$restored_summary"
  [[ $restored_summary == "$(cat "$directory/source-summary.txt")" ]] || {
    printf 'restored payment summary differs from source; do not cut over\n' >&2; exit 1;
  }
  psql -X -v ON_ERROR_STOP=1 "$connection" -f "$script_dir/inventory-payment.sql" \
    > "$directory/target-inventory.txt"
  disabled_triggers=$(psql -X -v ON_ERROR_STOP=1 -At "$connection" -c \
    "select count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='payment' and t.tgenabled='D'")
  [[ $disabled_triggers == 0 ]] || { printf 'disabled payment triggers remain\n' >&2; exit 1; }
  printf 'payment snapshot restored; inspect target-inventory.txt before cutover\n'
else
  usage
fi
