-- Stable order-side Commerce snapshot fields; safe to compare on source and target.
-- The legacy Credits wallet and journal tables are exported separately.
select jsonb_build_object(
  'products',(select count(*) from commerce.products),
  'prices',(select count(*) from commerce.prices),
  'orders',(select count(*) from commerce.orders),
  'paid_orders',(select count(*) from commerce.orders where status='paid'),
  'order_items',(select count(*) from commerce.order_items),
  'payment_intents',(select count(*) from commerce.payment_intents),
  'payment_attempts',(select count(*) from commerce.payment_attempts),
  'provider_events',(select count(*) from commerce.provider_events),
  'max_provider_event_id',(select coalesce(max(id),0) from commerce.provider_events),
  'refunds',(select count(*) from commerce.refunds),
  'succeeded_refunds',(select count(*) from commerce.refunds where status='succeeded'),
  'orphan_prices',(
    select count(*) from commerce.prices p left join commerce.products x on x.sku=p.sku
      where x.sku is null
  ),
  'orphan_order_items',(
    select count(*) from commerce.order_items i
      left join commerce.orders o on o.id=i.order_id
      left join commerce.prices p on p.id=i.price_id
      where o.id is null or p.id is null
  ),
  'order_item_price_mismatches',(
    select count(*) from commerce.order_items i join commerce.prices p on p.id=i.price_id
      where i.sku<>p.sku or i.amount_minor<>p.amount_minor or i.credits<>p.credits
  ),
  'orphan_payment_intents',(
    select count(*) from commerce.payment_intents i
      left join commerce.orders o on o.id=i.order_id where o.id is null
  ),
  'orphan_payment_attempts',(
    select count(*) from commerce.payment_attempts a
      left join commerce.payment_intents i on i.id=a.intent_id where i.id is null
  ),
  'orphan_provider_events',(
    select count(*) from commerce.provider_events e
      left join commerce.payment_attempts a on a.id=e.attempt_id where a.id is null
  ),
  'provider_event_attempt_mismatches',(
    select count(*) from commerce.provider_events e
      join commerce.payment_attempts a on a.id=e.attempt_id
      where e.provider<>a.provider or e.merchant_id<>a.merchant_id
  ),
  'orphan_refunds',(
    select count(*) from commerce.refunds r
      left join commerce.orders o on o.id=r.order_id where o.id is null
  ),
  'order_amount_mismatches',(
    select count(*) from commerce.orders o where o.amount_minor is distinct from
      (select coalesce(sum(i.amount_minor*i.quantity),0) from commerce.order_items i
        where i.order_id=o.id)
  ),
  'intent_amount_mismatches',(
    select count(*) from commerce.payment_intents i join commerce.orders o on o.id=i.order_id
      where i.amount_minor<>o.amount_minor or i.currency<>o.currency
  ),
  'paid_orders_missing_successful_attempt',(
    select count(*) from commerce.orders o where o.status='paid' and
      (o.paid_at is null or not exists(
        select 1 from commerce.payment_intents i
        join commerce.payment_attempts a on a.intent_id=i.id
        where i.order_id=o.id and i.status='succeeded' and a.status='succeeded'
          and a.provider_trade_no is not null))
  ),
  'refund_overdraw',(
    select count(*) from commerce.orders o where
      (select coalesce(sum(r.amount_minor),0) from commerce.refunds r
        where r.order_id=o.id and r.status='succeeded')>o.amount_minor
  ),
  'refund_on_unpaid_order',(
    select count(*) from commerce.refunds r join commerce.orders o on o.id=r.order_id
      where r.status='succeeded' and o.status<>'paid'
  )
);
