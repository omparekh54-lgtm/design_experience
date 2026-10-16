alter table instruments add column if not exists listed_from date;
alter table instruments add column if not exists delisted_on date;

create table if not exists shareholding_history (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  period_end date not null,
  filed_at timestamptz not null,
  promoter_holding_pct numeric,
  promoter_pledge_pct numeric,
  total_shares numeric,
  source text not null,
  revision_number integer not null default 1,
  raw_payload jsonb not null default '{}'::jsonb,
  unique (instrument_id, period_end, filed_at, source, revision_number)
);

create index if not exists shareholding_asof_idx
  on shareholding_history (instrument_id, period_end desc, filed_at desc);

create table if not exists shares_outstanding_history (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  effective_from date not null,
  effective_to date,
  shares_outstanding numeric not null,
  source text not null,
  knowable_at timestamptz not null,
  raw_payload jsonb not null default '{}'::jsonb,
  unique (instrument_id, effective_from, source, knowable_at)
);

create index if not exists shares_outstanding_asof_idx
  on shares_outstanding_history (instrument_id, effective_from desc, knowable_at desc);

create table if not exists sector_history (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  sector text not null,
  industry text,
  effective_from date not null,
  effective_to date,
  source text not null,
  unique (instrument_id, sector, effective_from, source)
);

create table if not exists index_constituents_history (
  id bigserial primary key,
  index_code text not null,
  instrument_id bigint not null references instruments(id),
  effective_from date not null,
  effective_to date,
  source text not null,
  unique (index_code, instrument_id, effective_from, source)
);

create index if not exists index_constituents_asof_idx
  on index_constituents_history (index_code, effective_from, effective_to);

create table if not exists corporate_actions (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  action_type text not null,
  ex_date date not null,
  record_date date,
  ratio_from numeric,
  ratio_to numeric,
  cash_amount numeric,
  source text not null,
  announced_at timestamptz,
  raw_payload jsonb not null default '{}'::jsonb,
  unique (instrument_id, action_type, ex_date, source)
);

create index if not exists corporate_actions_exdate_idx
  on corporate_actions (instrument_id, ex_date);

create table if not exists earnings_calendar (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  event_date date not null,
  announced_at timestamptz,
  source text not null,
  raw_payload jsonb not null default '{}'::jsonb,
  unique (instrument_id, event_date, source)
);

create table if not exists module_a_history (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  as_of timestamptz not null,
  eligible boolean not null,
  score numeric not null,
  failed_rules jsonb not null default '[]'::jsonb,
  triggers jsonb not null default '[]'::jsonb,
  flags jsonb not null default '[]'::jsonb,
  snapshot jsonb not null,
  strategy_version text not null,
  unique (instrument_id, as_of, strategy_version)
);

create table if not exists module_b_signals (
  id bigserial primary key,
  instrument_id bigint not null references instruments(id),
  signal_date date not null,
  setup text not null default 'SETUP_1',
  signal_high numeric not null,
  signal_low numeric not null,
  rally_start date,
  rally_high_date date,
  rally_pct numeric,
  velocity numeric,
  volume_ratio numeric,
  expires_on date,
  status text not null default 'ACTIVE',
  payload jsonb not null default '{}'::jsonb,
  strategy_version text not null,
  unique (instrument_id, signal_date, setup, strategy_version)
);

create table if not exists trade_state_events (
  id bigserial primary key,
  trade_id uuid not null,
  instrument_id bigint references instruments(id),
  event_time timestamptz not null,
  state text not null,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  strategy_version text not null
);

create index if not exists trade_state_events_trade_idx
  on trade_state_events (trade_id, event_time);
