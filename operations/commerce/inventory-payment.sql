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
  'event_chain_breaks', (
    select count(*) from (
      select e.previous_event_hash,lag(e.event_hash) over(order by e.sequence_no) as expected_previous
      from payment.payment_events e
    ) chain where previous_event_hash is distinct from expected_previous
  ),
  'event_hash_mismatches', (
    select count(*) from payment.payment_events e where e.event_hash is distinct from public.digest(
      're8ch.payment.event.v1|'||e.event_id::text||'|'||e.payment_id::text||'|'||
      e.source||'|'||e.event_type||'|'||e.normalized_payload::text||'|'||
      encode(coalesce(e.previous_event_hash,''::bytea),'hex'),'sha256')
  ),
  'audit_delivery_rows', (select count(*) from payment.audit_delivery),
  'audit_delivery_missing', (
    select count(*) from payment.payment_events e left join payment.audit_delivery d
      on d.sequence_no=e.sequence_no where d.sequence_no is null
  ),
  'audit_delivery_hash_mismatches', (
    select count(*) from payment.audit_delivery d
      where d.record_sha256 is distinct from public.digest(d.record_bytes,'sha256')
  ),
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
