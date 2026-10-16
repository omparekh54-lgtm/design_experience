alter table instruments add column if not exists exchange text;
alter table instruments add column if not exists trading_symbol text;
alter table instruments add column if not exists name text;
alter table instruments add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table instruments add column if not exists updated_at timestamptz not null default now();

create unique index if not exists instruments_exchange_trading_symbol_uq
  on instruments (exchange, trading_symbol)
  where exchange is not null and trading_symbol is not null;

create index if not exists instruments_angel_token_idx
  on instruments (angel_token)
  where angel_token is not null;

create index if not exists daily_prices_trade_date_idx
  on daily_prices (trade_date desc);

create table if not exists data_quality_issues (
  id bigserial primary key,
  dataset text not null,
  instrument_id bigint references instruments(id),
  severity text not null check (severity in ('INFO','WARNING','ERROR')),
  code text not null,
  message text not null,
  observed_at timestamptz not null,
  resolved_at timestamptz
);

create index if not exists data_quality_open_idx
  on data_quality_issues (severity, observed_at desc)
  where resolved_at is null;

create table if not exists ingestion_runs (
  id bigserial primary key,
  dataset text not null,
  provider text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  attempted integer not null default 0,
  written integer not null default 0,
  status text not null default 'RUNNING',
  metadata jsonb not null default '{}'::jsonb,
  error text
);

create table if not exists provider_snapshots (
  id bigserial primary key,
  provider text not null,
  dataset text not null,
  observed_at timestamptz not null default now(),
  content_sha256 text,
  source_uri text,
  metadata jsonb not null default '{}'::jsonb
);
