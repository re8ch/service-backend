-- Refuse an order snapshot restore over any target order-side Commerce rows.
do $$
declare v_table text; v_count bigint;
begin
  foreach v_table in array array['products','prices','orders','order_items',
    'payment_intents','payment_attempts','provider_events','refunds'] loop
    if to_regclass('commerce.'||v_table) is null then
      raise exception 'target Commerce table % is missing',v_table; end if;
    execute format('select count(*) from commerce.%I',v_table) into v_count;
    if v_count<>0 then raise exception 'target Commerce table % is not empty',v_table; end if;
  end loop;
end $$;
