-- Run after freezing Identity Credits writes. Output contains owner IDs: protect
-- it as payment data, keep it outside Git, and record a checksum of the export.
copy (
  select w.id as legacy_wallet_id,w.owner_kind,
    coalesce(w.user_id::text,w.tenant_ref,w.system_name) as owner_ref,w.balance as source_balance
  from credits.wallets w order by w.id
) to stdout with (format csv,header true);
