create table if not exists instruments (
  id bigserial primary key,
  isin text,
  nse_symbol text,
  bse_code text,
  angel_token text,
  sector text,
  active boolean not null default true,
  valid_from timestamptz not null default now(),
  valid_to timestamptz
);

create table if not exists daily_prices (
  instrument_id bigint not null references instruments(id),
  trade_date date not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume numeric not null,
  turnover numeric,
  source text not null,
  ingested_at timestamptz not null default now(),
  primary key (instrument_id, trade_date, source)
);

create table if not exists financial_filings (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  period_end date not null,
  filing_timestamp timestamptz not null,
  source text not null,
  consolidated boolean,
  revision_number integer not null default 1,
  raw_uri text,
  ingested_at timestamptz not null default now()
);

create index if not exists financial_filings_asof_idx
  on financial_filings (instrument_id, filing_timestamp desc);

create table if not exists financial_facts (
  filing_id bigint not null references financial_filings(id) on delete cascade,
  fact_name text not null,
  value numeric,
  unit text,
  primary key (filing_id, fact_name)
);

create table if not exists surveillance_history (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  surveillance_type text not null,
  stage text,
  effective_from timestamptz not null,
  effective_to timestamptz,
  source text not null
);

create table if not exists strategy_decisions (
  id bigserial primary key,
  instrument_id bigint references instruments(id),
  module text not null,
  as_of timestamptz not null,
  strategy_version text not null,
  decision jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists orders (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  mode text not null check (mode in ('BACKTEST','PAPER','LIVE_APPROVAL','LIVE_AUTO')),
  status text not null,
  side text not null,
  quantity integer not null,
  trigger_price numeric,
  limit_price numeric,
  broker_order_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists audit_log (
  id bigserial primary key,
  event_type text not null,
  actor text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
