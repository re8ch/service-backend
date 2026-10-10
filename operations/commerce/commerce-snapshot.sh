#!/usr/bin/env bash
set -euo pipefail
umask 077

# DBA-only snapshot of the old order-side Commerce tables. The legacy wallet
# and Credits journal use separate CSV exports and billing-import.sh.
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
summary() {
  psql -X -v ON_ERROR_STOP=1 -At "$connection" -f "$script_dir/commerce-snapshot-summary.sql"
}
require_integrity() {
  local report=$1 field
  for field in orphan_prices orphan_order_items orphan_payment_intents \
    orphan_payment_attempts orphan_provider_events orphan_refunds \
    order_item_price_mismatches provider_event_attempt_mismatches \
    order_amount_mismatches intent_amount_mismatches \
    paid_orders_missing_successful_attempt refund_overdraw refund_on_unpaid_order; do
    [[ $report == *\"$field\":\ 0* ]] || {
      printf 'Commerce snapshot integrity failed: %s\n' "$field" >&2
      exit 1
    }
  done
}
tables=(products prices orders order_items payment_intents payment_attempts provider_events refunds)
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
  summary > "$directory/source-summary.txt"
  require_integrity "$(cat "$directory/source-summary.txt")"
  dump_args=(--format=custom --data-only --no-owner --no-acl --dbname="$connection")
  for table in "${tables[@]}"; do dump_args+=(--table="commerce.$table"); done
  dump_args+=(--table=commerce.provider_events_id_seq)
  pg_dump "${dump_args[@]}" --file="$directory/commerce.dump"
  [[ $(summary) == "$(cat "$directory/source-summary.txt")" ]] || {
    printf 'source changed during Commerce snapshot; discard dump and freeze writes\n' >&2
    exit 1
  }
  "${checksum[@]}" "$directory/commerce.dump" > "$directory/commerce.dump.sha256"
  printf 'Commerce order snapshot exported to %s\n' "$directory"
elif [[ $action == restore ]]; then
  [[ -f $directory/commerce.dump && -f $directory/commerce.dump.sha256 &&
     -f $directory/source-summary.txt ]] || usage
  (cd -- "$directory" && "${checksum[@]}" -c commerce.dump.sha256)
  psql -X -v ON_ERROR_STOP=1 "$connection" -f "$script_dir/commerce-target-empty.sql"
  pg_restore --data-only --disable-triggers --single-transaction --exit-on-error \
    --no-owner --no-acl --dbname="$connection" "$directory/commerce.dump"
  restored_summary=$(summary)
  require_integrity "$restored_summary"
  [[ $restored_summary == "$(cat "$directory/source-summary.txt")" ]] || {
    printf 'restored Commerce summary differs from source; do not cut over\n' >&2
    exit 1
  }
  disabled_triggers=$(psql -X -v ON_ERROR_STOP=1 -At "$connection" -c \
    "select count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='commerce' and t.tgenabled='D'")
  [[ $disabled_triggers == 0 ]] || { printf 'disabled Commerce triggers remain\n' >&2; exit 1; }
  printf 'Commerce order snapshot restored; reconcile paid orders with the legacy journal\n'
else
  usage
fi
