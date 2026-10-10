-- Run with psql against the existing identity database using a read-only role.
-- Aggregate output only; preserve the query result with the migration evidence.
begin transaction isolation level repeatable read read only;
select jsonb_pretty(jsonb_build_object(
  'database', current_database(),
  'captured_at', now(),
  'postgres_major', current_setting('server_version_num')::integer / 10000,
  'wallets', (select count(*) from credits.wallets),
  'personal_wallets', (select count(*) from credits.wallets where owner_kind='personal'),
  'organization_wallets', (select count(*) from credits.wallets where owner_kind='organization'),
  'wallet_balance_sum', (select coalesce(sum(balance),0) from credits.wallets),
  'topups', (select count(*) from credits.topups),
  'purchase_rates', (select count(*) from credits.purchase_rates),
  'service_prices', (select count(*) from credits.service_prices),
  'uncredited_topups', (select count(*) from credits.topups where credited_at is null),
  'bound_topups', (select count(*) from credits.topups where payment_id is not null),
  'credited_unbound_topups', (select count(*) from credits.topups
    where credited_at is not null and payment_id is null),
  'refunds', (select count(*) from credits.refunds),
  'quotes', (select count(*) from credits.quotes),
  'accepted_quotes', (select count(*) from credits.quotes where accepted_at is not null),
  'transactions', (select count(*) from credits.transactions),
  'max_sequence_no', (select coalesce(max(sequence_no),0) from credits.transactions),
  'entry_sum', (select coalesce(sum(amount),0) from credits.entries),
  'malformed_transactions', (
    select count(*) from (
      select t.id from credits.transactions t left join credits.entries e on e.transaction_id=t.id
      group by t.id having count(e.wallet_id)<>2
        or count(*) filter(where e.amount>0)<>1
        or count(*) filter(where e.amount<0)<>1
        or coalesce(sum(e.amount),0)<>0
    ) bad
  ),
  'wallet_balance_mismatches', (
    select count(*) from credits.wallets w where w.balance is distinct from
      (select coalesce(sum(e.amount),0) from credits.entries e where e.wallet_id=w.id)
  )
)) as inventory;
commit;
