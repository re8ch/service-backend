-- Run against a frozen Identity Credits database. This export contains payer
-- identifiers and payment references: store it outside Git with restricted access.
-- The CSV has one JSON record per row so related source tables remain in one
-- independently checksummed snapshot. The record_type gives the source table.
copy (
  select record_type, record from (
    select 'purchase_rate'::text as record_type, to_jsonb(r) as record, r.id::text as record_id
      from credits.purchase_rates r
    union all
    select 'topup', to_jsonb(t), t.id::text from credits.topups t
    union all
    select 'refund', to_jsonb(r), r.refund_ref from credits.refunds r
    union all
    select 'service_price', to_jsonb(p), p.id::text from credits.service_prices p
    union all
    select 'quote', to_jsonb(q), q.id::text from credits.quotes q
  ) source_rows
  order by record_type, record_id
) to stdout with (format csv,header true);
