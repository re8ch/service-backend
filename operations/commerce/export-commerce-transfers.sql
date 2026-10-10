-- Run after freezing old Commerce writes. The inventory must report zero
-- malformed credit transactions and every exported wallet ID must be nonnull.
copy (
  select t.id as legacy_transaction_id,t.kind as legacy_kind,t.source_ref as legacy_source_ref,
    case when debit.account='system:issuance' then
      '00000000-0000-4000-8000-000000000001'::uuid else from_wallet.id end as from_wallet_id,
    case when credit.account='system:issuance' then
      '00000000-0000-4000-8000-000000000001'::uuid else to_wallet.id end as to_wallet_id,
    credit.amount,t.created_at
  from commerce.credit_transactions t
    join commerce.credit_entries debit on debit.transaction_id=t.id and debit.amount<0
    join commerce.credit_entries credit on credit.transaction_id=t.id and credit.amount>0
    left join commerce.wallets from_wallet on debit.account='user:'||from_wallet.owner_subject::text
    left join commerce.wallets to_wallet on credit.account='user:'||to_wallet.owner_subject::text
  where -debit.amount=credit.amount
  order by t.created_at,t.id
) to stdout with (format csv,header true);
