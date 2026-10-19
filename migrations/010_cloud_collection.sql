-- Reviewed deployment migration; not run automatically by the collector.
-- Preserve the external backfill and add a separate writable raw-price layer.
-- Apply as one transaction with search_path explicitly set to pms,public.
do $$ begin
  if current_schema()<>'pms' then
    raise exception 'Set search_path to pms,public before reviewing/applying migration 010';
  end if;
end; $$;
create table if not exists cloud_daily_prices (
  instrument_id bigint not null references instruments(id),
  trade_date date not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume numeric not null,
  turnover numeric not null,
  source text not null default 'NSE_UDIFF',
  ingested_at timestamptz not null default now(),
  primary key(instrument_id,trade_date,source),
  check(open>0 and low>0 and close>0 and high>=greatest(open,close,low) and low<=least(open,close) and volume>=0 and turnover>=0),
  check(open::text not in ('NaN','Infinity','-Infinity') and high::text not in ('NaN','Infinity','-Infinity') and low::text not in ('NaN','Infinity','-Infinity') and close::text not in ('NaN','Infinity','-Infinity') and volume::text not in ('NaN','Infinity','-Infinity') and turnover::text not in ('NaN','Infinity','-Infinity'))
);

do $$
declare kind "char"; original text; columns text[];
begin
  select c.relkind into kind from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname=current_schema() and c.relname='daily_prices';
  if kind='v' then
    select array_agg(a.attname::text order by a.attnum) into columns from pg_attribute a
      where a.attrelid='daily_prices'::regclass and a.attnum>0 and not a.attisdropped;
    if columns<>array['instrument_id','trade_date','open','high','low','close','volume','turnover','source','ingested_at'] then
      raise exception 'Unexpected daily_prices view contract; manual migration review required';
    end if;
    original=rtrim(pg_get_viewdef('daily_prices'::regclass,true),E';\n ');
    if position('cloud_daily_prices' in original)=0 then
      execute 'create or replace view daily_prices as '||original||
        ' UNION ALL SELECT instrument_id,trade_date,open,high,low,close,volume,turnover,source,ingested_at FROM cloud_daily_prices';
    end if;
  elsif kind not in ('r','p') or kind is null then
    raise exception 'daily_prices missing or unsupported; manual migration review required';
  end if;
end;
$$;

-- Internal source data: no public Data API grants. Application-role grants are
-- deployment-specific and must be reviewed, not guessed by the collector.
revoke all on cloud_daily_prices from public;
alter table cloud_daily_prices enable row level security;
create index if not exists provider_snapshots_record_queue_idx
  on provider_snapshots(provider,dataset,(metadata->>'record_key'));
