create table if not exists market_regime_history (
  trade_date date primary key,
  raw_score numeric not null,
  smoothed_score numeric not null,
  band text not null,
  max_open_trades integer not null,
  hard_override boolean not null default false,
  components jsonb not null default '{}'::jsonb,
  inputs jsonb not null default '{}'::jsonb,
  strategy_version text not null
);

create table if not exists module_c_rankings (
  id bigserial primary key,
  trade_date date not null,
  instrument_id bigint not null references instruments(id),
  score numeric not null,
  qualified boolean not null,
  selected boolean not null default false,
  components jsonb not null default '{}'::jsonb,
  rejected_reasons jsonb not null default '[]'::jsonb,
  strategy_version text not null,
  unique (trade_date, instrument_id, strategy_version)
);

create table if not exists research_dataset_manifests (
  id uuid primary key,
  created_at timestamptz not null default now(),
  start_date date not null,
  end_date date not null,
  universe text not null,
  root_path text not null,
  tables jsonb not null,
  strategy_version text not null,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists research_runs (
  id uuid primary key,
  created_at timestamptz not null default now(),
  run_type text not null,
  variant text not null,
  start_date date,
  end_date date,
  config jsonb not null,
  metrics jsonb not null,
  dataset_manifest_id uuid references research_dataset_manifests(id),
  strategy_version text not null
);

create table if not exists robustness_points (
  id bigserial primary key,
  research_run_id uuid not null references research_runs(id) on delete cascade,
  parameters jsonb not null,
  score numeric not null,
  metrics jsonb not null
);

create index if not exists research_runs_type_idx on research_runs (run_type, created_at desc);
create index if not exists module_c_rankings_date_idx on module_c_rankings (trade_date, selected, score desc);
