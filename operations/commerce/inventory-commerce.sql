-- Run with psql against the existing commerce database using a read-only role.
begin transaction isolation level repeatable read read only;
select jsonb_pretty(jsonb_build_object(
  'database', current_database(),
  'captured_at', now(),
  'postgres_major', current_setting('server_version_num')::integer / 10000,
  'orders', (select count(*) from commerce.orders),
  'paid_orders', (select count(*) from commerce.orders where status='paid'),
  'pending_orders', (select count(*) from commerce.orders where status='pending'),
  'payment_attempts', (select count(*) from commerce.payment_attempts),
  'unsettled_attempts', (select count(*) from commerce.payment_attempts where status in ('pending','processing')),
  'provider_events', (select count(*) from commerce.provider_events),
  'refunds', (select count(*) from commerce.refunds),
  'wallets', (select count(*) from commerce.wallets),
  'wallet_balance_sum', (select coalesce(sum(balance),0) from commerce.wallets),
  'credit_transactions', (select count(*) from commerce.credit_transactions),
  'credit_entry_sum', (select coalesce(sum(amount),0) from commerce.credit_entries),
  'wallet_balance_mismatches', (
    select count(*) from commerce.wallets w where w.balance is distinct from
      (select coalesce(sum(e.amount),0) from commerce.credit_entries e
        where e.account='user:'||w.owner_subject::text)
  )
)) as inventory;
commit;
