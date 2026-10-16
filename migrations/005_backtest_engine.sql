create table if not exists trades (
  id uuid primary key,
  instrument_id bigint not null references instruments(id),
  signal_date date not null,
  state text not null,
  entry numeric,
  stop numeric,
  shares integer not null default 0,
  remaining_shares integer not null default 0,
  realized_pnl numeric not null default 0,
  exit_reason text,
  strategy_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists backtest_runs (
  id uuid primary key,
  strategy_version text not null,
  started_on date not null,
  ended_on date not null,
  initial_capital numeric not null,
  ending_cash numeric,
  realized_gross_pnl numeric,
  total_costs numeric,
  realized_net_pnl numeric,
  return_pct numeric,
  config jsonb not null,
  status text not null default 'RUNNING',
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists backtest_events (
  id bigserial primary key,
  run_id uuid not null references backtest_runs(id) on delete cascade,
  event_time timestamptz not null,
  event_type text not null,
  instrument_id bigint references instruments(id),
  payload jsonb not null default '{}'::jsonb
);

create table if not exists backtest_fills (
  id bigserial primary key,
  run_id uuid not null references backtest_runs(id) on delete cascade,
  instrument_id bigint not null references instruments(id),
  trade_date date not null,
  side text not null,
  price numeric not null,
  quantity integer not null,
  reason text not null,
  costs jsonb not null default '{}'::jsonb
);

create index if not exists backtest_events_run_time_idx on backtest_events(run_id, event_time);
create index if not exists backtest_fills_run_date_idx on backtest_fills(run_id, trade_date);
