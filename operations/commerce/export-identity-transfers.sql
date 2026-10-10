-- Run after freezing Identity Credits writes. The inventory must report zero
-- malformed transactions. Preserve bytes and row order; import by sequence_no.
copy (
  select t.id as legacy_transaction_id,t.kind as legacy_kind,t.source_ref as legacy_source_ref,
    debit.wallet_id as from_wallet_id,credit.wallet_id as to_wallet_id,
    credit.amount,t.sequence_no,
    encode(t.previous_hash,'hex') as previous_hash_hex,
    encode(t.entry_hash,'hex') as entry_hash_hex,t.payment_id,t.created_at
  from credits.transactions t
    join credits.entries debit on debit.transaction_id=t.id and debit.amount<0
    join credits.entries credit on credit.transaction_id=t.id and credit.amount>0
  where -debit.amount=credit.amount
  order by t.sequence_no
) to stdout with (format csv,header true);
