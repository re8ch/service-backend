-- Run after freezing old Commerce writes. Its system issuance account has no
-- wallet row; the fixed UUID below is a migration-only source identifier.
copy (
  select w.id as legacy_wallet_id,'personal'::text as owner_kind,
    w.owner_subject::text as owner_ref,w.balance as source_balance
  from commerce.wallets w
  union all
  select '00000000-0000-4000-8000-000000000001'::uuid,'system','issuance',
    (select coalesce(sum(amount),0) from commerce.credit_entries where account='system:issuance')
  order by legacy_wallet_id
) to stdout with (format csv,header true);
