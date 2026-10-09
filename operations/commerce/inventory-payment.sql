-- Run with psql against the existing payment-registry database using a read-only role.
begin transaction isolation level repeatable read read only;
select jsonb_pretty(jsonb_build_object(
  'database', current_database(),
  'captured_at', now(),
  'postgres_major', current_setting('server_version_num')::integer / 10000,
  'payments', (select count(*) from payment.payments),
  'credits_payments', (select count(*) from payment.payments where business_reference like 'credits:%'),
  'pending_payments', (select count(*) from payment.payments where status in ('requires_action','processing')),
  'succeeded_payments', (select count(*) from payment.payments where status='succeeded'),
  'attempts', (select count(*) from payment.payment_attempts),
  'events', (select count(*) from payment.payment_events),
  'max_event_sequence', (select coalesce(max(sequence_no),0) from payment.payment_events),
  'refunds', (select count(*) from payment.refunds),
  'pending_credit_outbox', (
    select count(*) from payment.outbox_events where delivered_at is null
      and business_reference like 'credits:%'
      and event_type in ('payment.succeeded','refund.succeeded')
  ),
  'leased_credit_outbox', (
    select count(*) from payment.outbox_events where delivered_at is null
      and lease_until > now() and business_reference like 'credits:%'
      and event_type in ('payment.succeeded','refund.succeeded')
  )
)) as inventory;
commit;
