-- Stable payment-registry fields to compare before and after a frozen snapshot.
-- Run with psql -X -At; the single JSON line contains aggregates only.
select jsonb_build_object(
  'payments',(select count(*) from payment.payments),
  'attempts',(select count(*) from payment.payment_attempts),
  'events',(select count(*) from payment.payment_events),
  'event_sequence',(select coalesce(max(sequence_no),0) from payment.payment_events),
  'refunds',(select count(*) from payment.refunds),
  'outbox',(select count(*) from payment.outbox_events),
  'pending_credit_outbox',(
    select count(*) from payment.outbox_events where delivered_at is null
      and business_reference like 'credits:%'
      and event_type in ('payment.succeeded','refund.succeeded')
  ),
  'outbox_sequence',(select coalesce(max(id),0) from payment.outbox_events),
  'audit_batches',(select count(*) from payment.audit_batches),
  'audit_delivery',(select count(*) from payment.audit_delivery),
  'audit_base_anchors',(select count(*) from payment.audit_base_anchors),
  'audit_batch_manifests',(select count(*) from payment.audit_batch_manifests),
  'audit_batch_members',(select count(*) from payment.audit_batch_members),
  'attempt_orphans',(
    select count(*) from payment.payment_attempts a left join payment.payments p on p.id=a.payment_id
    where p.id is null
  ),
  'event_orphans',(
    select count(*) from payment.payment_events e left join payment.payments p on p.id=e.payment_id
      left join payment.payment_attempts a on a.id=e.attempt_id
    where p.id is null or (e.attempt_id is not null and a.id is null)
  ),
  'refund_orphans',(
    select count(*) from payment.refunds r left join payment.payments p on p.id=r.payment_id
    where p.id is null
  ),
  'outbox_orphans',(
    select count(*) from payment.outbox_events o left join payment.payments p on p.id=o.payment_id
    where p.id is null
  ),
  'event_chain_breaks',(
    select count(*) from (
      select previous_event_hash,lag(event_hash) over(order by sequence_no) as expected_previous
      from payment.payment_events
    ) e where previous_event_hash is distinct from expected_previous
  ),
  'event_hash_mismatches',(
    select count(*) from payment.payment_events e where e.event_hash is distinct from public.digest(
      're8ch.payment.event.v1|'||e.event_id::text||'|'||e.payment_id::text||'|'||
      e.source||'|'||e.event_type||'|'||e.normalized_payload::text||'|'||
      encode(coalesce(e.previous_event_hash,''::bytea),'hex'),'sha256')
  ),
  'audit_delivery_missing',(
    select count(*) from payment.payment_events e left join payment.audit_delivery d
      on d.sequence_no=e.sequence_no where d.sequence_no is null
  ),
  'audit_delivery_orphans',(
    select count(*) from payment.audit_delivery d left join payment.payment_events e
      on e.sequence_no=d.sequence_no where e.sequence_no is null
  ),
  'audit_base_orphans',(
    select count(*) from payment.audit_base_anchors a left join payment.audit_delivery d
      on d.sequence_no=a.sequence_no where d.sequence_no is null
  ),
  'audit_member_orphans',(
    select count(*) from payment.audit_batch_members m
      left join payment.audit_batch_manifests b on b.batch_no=m.batch_no
      left join payment.audit_base_anchors a on a.sequence_no=m.sequence_no
    where b.batch_no is null or a.sequence_no is null
  ),
  'audit_delivery_hash_mismatches',(
    select count(*) from payment.audit_delivery d
      where d.record_sha256 is distinct from public.digest(d.record_bytes,'sha256')
  )
);
