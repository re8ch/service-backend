#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run only against a new re8ch_billing database after its payment snapshot and
# migrations 046-051. Source CSVs must come from frozen source databases.
if [[ $# != 7 ]]; then
  printf 'usage: %s TARGET_SERVICE IDENTITY_WALLETS IDENTITY_TRANSFERS COMMERCE_WALLETS COMMERCE_TRANSFERS IDENTITY_RECORDS TENANT_RESOLUTIONS\n' "$0" >&2
  exit 2
fi
target_service=$1
identity_wallets=$2
identity_transfers=$3
commerce_wallets=$4
commerce_transfers=$5
identity_records=$6
tenant_resolutions=$7
[[ $target_service =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
for file in "$identity_wallets" "$identity_transfers" "$commerce_wallets" \
  "$commerce_transfers" "$identity_records" "$tenant_resolutions"; do
  [[ $file == /* && -f $file && $file != *"'"* && $file != *'\'* &&
     $file != *$'\n'* && $file != *$'\r'* ]] || {
    printf 'CSV path must be an existing safe absolute path\n' >&2
    exit 2
  }
done
command -v psql >/dev/null
command -v python3 >/dev/null
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/analyze-wallet-overlap.py" "$identity_wallets" "$commerce_wallets"

sql_file=$(mktemp /tmp/re8ch-billing-import.XXXXXX)
trap 'rm "$sql_file"' EXIT
cat > "$sql_file" <<'SQL'
\set ON_ERROR_STOP on
begin;
create temp table stage_identity_wallets(ordinal bigint generated always as identity,
  legacy_wallet_id uuid,owner_kind text,owner_ref text,source_balance bigint);
create temp table stage_identity_transfers(ordinal bigint generated always as identity,
  legacy_transaction_id uuid,legacy_kind text,legacy_source_ref text,
  from_wallet_id uuid,to_wallet_id uuid,amount bigint,sequence_no bigint,
  previous_hash_hex text,entry_hash_hex text,payment_id uuid,created_at timestamptz);
create temp table stage_commerce_wallets(ordinal bigint generated always as identity,
  legacy_wallet_id uuid,owner_kind text,owner_ref text,source_balance bigint);
create temp table stage_commerce_transfers(ordinal bigint generated always as identity,
  legacy_transaction_id uuid,legacy_kind text,legacy_source_ref text,
  from_wallet_id uuid,to_wallet_id uuid,amount bigint,created_at timestamptz);
create temp table stage_identity_records(ordinal bigint generated always as identity,
  record_type text,record jsonb);
create temp table stage_tenant_resolutions(legacy_topup_id uuid primary key,
  tenant_id text not null,evidence_ref text not null);
SQL
printf "\\copy stage_identity_wallets(legacy_wallet_id,owner_kind,owner_ref,source_balance) from '%s' with (format csv,header true)\n" "$identity_wallets" >> "$sql_file"
printf "\\copy stage_identity_transfers(legacy_transaction_id,legacy_kind,legacy_source_ref,from_wallet_id,to_wallet_id,amount,sequence_no,previous_hash_hex,entry_hash_hex,payment_id,created_at) from '%s' with (format csv,header true)\n" "$identity_transfers" >> "$sql_file"
printf "\\copy stage_commerce_wallets(legacy_wallet_id,owner_kind,owner_ref,source_balance) from '%s' with (format csv,header true)\n" "$commerce_wallets" >> "$sql_file"
printf "\\copy stage_commerce_transfers(legacy_transaction_id,legacy_kind,legacy_source_ref,from_wallet_id,to_wallet_id,amount,created_at) from '%s' with (format csv,header true)\n" "$commerce_transfers" >> "$sql_file"
printf "\\copy stage_identity_records(record_type,record) from '%s' with (format csv,header true)\n" "$identity_records" >> "$sql_file"
printf "\\copy stage_tenant_resolutions(legacy_topup_id,tenant_id,evidence_ref) from '%s' with (format csv,header true)\n" "$tenant_resolutions" >> "$sql_file"
cat >> "$sql_file" <<'SQL'
do $$
declare v_row record; v_result jsonb; v_key text; v_value text;
begin
  if exists(select 1 from billing.legacy_wallet_map) or
    exists(select 1 from billing.legacy_transaction_map) or
    exists(select 1 from billing.accounts where owner_kind<>'system') or
    exists(select 1 from billing.transactions) or
    exists(select 1 from billing.purchase_rates) or
    exists(select 1 from billing.service_prices) or
    exists(select 1 from billing.topups) or
    exists(select 1 from billing.topup_refunds) or
    exists(select 1 from billing.quotes) then
    raise exception 'target billing database is not empty';
  end if;
  if exists(select 1 from stage_tenant_resolutions where
    nullif(trim(tenant_id),'') is null or nullif(trim(evidence_ref),'') is null) then
    raise exception 'tenant resolution requires tenant and evidence'; end if;
  if exists(select 1 from stage_identity_records r where r.record_type='topup'
    and r.record->>'payment_id' is null and not exists(
      select 1 from stage_tenant_resolutions t
      where t.legacy_topup_id=(r.record->>'id')::uuid)) then
    raise exception 'unbound topup lacks verified tenant resolution'; end if;
  if exists(select 1 from stage_tenant_resolutions t where not exists(
    select 1 from stage_identity_records r where r.record_type='topup'
      and r.record->>'payment_id' is null
      and (r.record->>'id')::uuid=t.legacy_topup_id)) then
    raise exception 'tenant resolution does not match an unbound topup'; end if;
  if exists(select 1 from stage_identity_wallets w where w.source_balance is distinct from
    (select coalesce(sum(case when t.to_wallet_id=w.legacy_wallet_id then t.amount
      else -t.amount end),0) from stage_identity_transfers t
      where w.legacy_wallet_id in (t.from_wallet_id,t.to_wallet_id))) then
    raise exception 'Identity CSV wallet balance differs from exported transfers'; end if;
  if exists(select 1 from stage_commerce_wallets w where w.source_balance is distinct from
    (select coalesce(sum(case when t.to_wallet_id=w.legacy_wallet_id then t.amount
      else -t.amount end),0) from stage_commerce_transfers t
      where w.legacy_wallet_id in (t.from_wallet_id,t.to_wallet_id))) then
    raise exception 'Commerce CSV wallet balance differs from exported transfers'; end if;
  if exists(
    select 1 from commerce.orders o
    left join commerce.order_items i on i.order_id=o.id and i.line_no=1
    left join stage_commerce_transfers t on t.legacy_kind='purchase'
      and t.legacy_source_ref='order:'||o.id::text
    left join stage_commerce_wallets w on w.legacy_wallet_id=t.to_wallet_id
    where (o.status='paid') is distinct from (t.legacy_transaction_id is not null)
      or (o.status='paid' and (t.amount is distinct from i.credits
        or w.owner_kind is distinct from 'personal'
        or w.owner_ref is distinct from o.payer_subject::text))
  ) or exists(
    select 1 from stage_commerce_transfers t where t.legacy_kind='purchase'
      and not exists(select 1 from commerce.orders o where o.status='paid'
        and t.legacy_source_ref='order:'||o.id::text)
  ) then
    raise exception 'Commerce paid orders do not match source purchase transfers'; end if;
  if exists(
    select 1 from commerce.refunds r join commerce.orders o on o.id=r.order_id
    left join stage_commerce_transfers t on t.legacy_kind='refund'
      and t.legacy_source_ref='refund:'||r.provider_ref
    left join stage_commerce_wallets w on w.legacy_wallet_id=t.from_wallet_id
    where (r.status='succeeded' and r.credits_reversed>0)
      is distinct from (t.legacy_transaction_id is not null)
      or (r.status='succeeded' and r.credits_reversed>0 and
        (t.amount is distinct from r.credits_reversed
          or w.owner_kind is distinct from 'personal'
          or w.owner_ref is distinct from o.payer_subject::text))
  ) or exists(
    select 1 from stage_commerce_transfers t where t.legacy_kind='refund'
      and not exists(select 1 from commerce.refunds r where r.status='succeeded'
        and r.credits_reversed>0
        and t.legacy_source_ref='refund:'||r.provider_ref)
  ) then
    raise exception 'Commerce refunds do not match source refund transfers'; end if;
  for v_row in select * from stage_identity_wallets order by ordinal loop
    perform billing.map_legacy_wallet('identity',v_row.legacy_wallet_id,
      v_row.owner_kind,v_row.owner_ref,v_row.source_balance);
  end loop;
  for v_row in select * from stage_commerce_wallets order by ordinal loop
    perform billing.map_legacy_wallet('commerce',v_row.legacy_wallet_id,
      v_row.owner_kind,v_row.owner_ref,v_row.source_balance);
  end loop;
  for v_row in select * from stage_identity_transfers order by sequence_no,ordinal loop
    perform billing.import_legacy_transfer('identity',v_row.legacy_transaction_id,
      v_row.legacy_kind,v_row.legacy_source_ref,v_row.from_wallet_id,v_row.to_wallet_id,
      v_row.amount,v_row.sequence_no,decode(nullif(v_row.previous_hash_hex,''),'hex'),
      decode(nullif(v_row.entry_hash_hex,''),'hex'),v_row.payment_id,v_row.created_at);
  end loop;
  v_result:=billing.verify_legacy_wallets('identity');
  if (v_result->>'owner_balance_mismatches')::bigint<>0 or
    (v_result->>'wallets')::bigint<>(select count(*) from stage_identity_wallets) or
    (v_result->>'transactions')::bigint<>(select count(*) from stage_identity_transfers) then
    raise exception 'Identity wallet import mismatch: %',v_result; end if;
  for v_row in select * from stage_commerce_transfers order by created_at,legacy_transaction_id loop
    perform billing.import_legacy_transfer('commerce',v_row.legacy_transaction_id,
      v_row.legacy_kind,v_row.legacy_source_ref,v_row.from_wallet_id,v_row.to_wallet_id,
      v_row.amount,null,null,null,null,v_row.created_at);
  end loop;
  v_result:=billing.verify_legacy_wallets('commerce');
  if (v_result->>'owner_balance_mismatches')::bigint<>0 or
    (v_result->>'wallets')::bigint<>(select count(*) from stage_commerce_wallets) or
    (v_result->>'transactions')::bigint<>(select count(*) from stage_commerce_transfers) then
    raise exception 'Commerce wallet import mismatch: %',v_result; end if;
  for v_row in
    select r.record_type,r.record,t.tenant_id from stage_identity_records r
    left join stage_tenant_resolutions t on r.record_type='topup'
      and (r.record->>'id')::uuid=t.legacy_topup_id
    order by r.ordinal
  loop
    perform billing.import_legacy_identity_record(v_row.record_type,v_row.record,v_row.tenant_id);
  end loop;
  v_result:=billing.verify_legacy_identity_metadata();
  for v_key,v_value in select key,value from jsonb_each_text(v_result) loop
    if v_key not in ('purchase_rates','service_prices','topups','refunds','quotes')
      and v_value::bigint<>0 then
      raise exception 'Identity metadata mismatch %: %',v_key,v_value; end if;
  end loop;
  if (v_result->>'purchase_rates')::bigint<>
      (select count(*) from stage_identity_records where record_type='purchase_rate') or
    (v_result->>'service_prices')::bigint<>
      (select count(*) from stage_identity_records where record_type='service_price') or
    (v_result->>'topups')::bigint<>
      (select count(*) from stage_identity_records where record_type='topup') or
    (v_result->>'refunds')::bigint<>
      (select count(*) from stage_identity_records where record_type='refund') or
    (v_result->>'quotes')::bigint<>
      (select count(*) from stage_identity_records where record_type='quote') then
    raise exception 'Identity metadata counts do not match source CSV'; end if;
end $$;
select billing.verify_legacy_wallets('identity') as identity_wallets;
select billing.verify_legacy_wallets('commerce') as commerce_wallets;
select billing.verify_legacy_identity_metadata() as identity_metadata;
commit;
SQL
psql -X -v ON_ERROR_STOP=1 "service=$target_service" -f "$sql_file"
