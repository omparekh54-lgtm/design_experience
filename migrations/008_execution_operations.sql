create table if not exists worker_runs (
  id uuid primary key,
  worker_name text not null,
  job_name text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'RUNNING',
  details jsonb not null default '{}'::jsonb
);

create table if not exists broker_orders (
  id uuid primary key,
  broker text not null,
  mode text not null,
  instrument_id bigint not null references instruments(id),
  side text not null,
  order_type text not null,
  quantity integer not null,
  limit_price numeric,
  trigger_price numeric,
  status text not null,
  external_order_id text,
  strategy_trade_id uuid references trades(id),
  idempotency_key text not null unique,
  raw_response jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists broker_fills (
  id uuid primary key,
  order_id uuid not null references broker_orders(id) on delete cascade,
  external_trade_id text,
  fill_time timestamptz not null,
  price numeric not null,
  quantity integer not null,
  fees numeric not null default 0,
  raw_response jsonb not null default '{}'::jsonb,
  unique (order_id, external_trade_id)
);

create table if not exists portfolio_ledger (
  id bigserial primary key,
  event_time timestamptz not null default now(),
  account_mode text not null,
  event_type text not null,
  instrument_id bigint references instruments(id),
  order_id uuid references broker_orders(id),
  quantity integer not null default 0,
  price numeric,
  cash_delta numeric not null default 0,
  realized_pnl_delta numeric not null default 0,
  fees numeric not null default 0,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists portfolio_snapshots (
  snapshot_date date not null,
  account_mode text not null,
  cash numeric not null,
  market_value numeric not null,
  realized_pnl numeric not null,
  unrealized_pnl numeric not null,
  nav numeric not null,
  open_risk numeric not null default 0,
  portfolio_heat_pct numeric not null default 0,
  positions jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  primary key (snapshot_date, account_mode)
);

create table if not exists reconciliation_runs (
  id uuid primary key,
  broker text not null,
  account_mode text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status text not null default 'RUNNING',
  orders_match boolean,
  positions_match boolean,
  cash_match boolean,
  discrepancies jsonb not null default '[]'::jsonb
);

create table if not exists safety_state (
  singleton boolean primary key default true check (singleton),
  execution_mode text not null default 'PAPER',
  new_orders_enabled boolean not null default false,
  emergency_stop boolean not null default false,
  locked_reason text,
  updated_at timestamptz not null default now(),
  updated_by text not null default 'system'
);

insert into safety_state(singleton, execution_mode, new_orders_enabled, emergency_stop, updated_by)
values (true, 'PAPER', false, false, 'migration')
on conflict (singleton) do nothing;

create index if not exists broker_orders_status_idx on broker_orders(status, created_at desc);
create index if not exists portfolio_ledger_time_idx on portfolio_ledger(account_mode, event_time);
create index if not exists reconciliation_runs_time_idx on reconciliation_runs(started_at desc);
create index if not exists worker_runs_time_idx on worker_runs(worker_name, started_at desc);
