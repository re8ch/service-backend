-- Refuse to restore payment rows over existing target payment rows.
do $$
declare v_table record; v_count bigint; v_tables integer:=0;
begin
  for v_table in
    select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='payment' and c.relkind in ('r','p') order by c.relname
  loop
    v_tables:=v_tables+1;
    execute format('select count(*) from payment.%I',v_table.relname) into v_count;
    if v_count<>0 then raise exception 'target payment table % is not empty',v_table.relname; end if;
  end loop;
  if v_tables<>10 then raise exception 'unexpected payment table count: %',v_tables; end if;
end $$;
