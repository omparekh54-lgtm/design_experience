create table if not exists stress_test_runs (
  id uuid primary key,
  variant text not null,
  symbols jsonb not null,
  initial_capital numeric,
  created_at timestamptz not null default now(),
  summary jsonb not null default '{}'::jsonb
);

create table if not exists stress_test_results (
  id bigserial primary key,
  run_id uuid not null references stress_test_runs(id) on delete cascade,
  period_name text not null,
  start_date date not null,
  end_date date not null,
  description text,
  metrics jsonb not null,
  unique (run_id, period_name)
);

create table if not exists walk_forward_runs (
  id uuid primary key,
  variant text not null,
  symbols jsonb not null,
  initial_capital numeric,
  created_at timestamptz not null default now(),
  summary jsonb not null default '{}'::jsonb
);

create table if not exists walk_forward_results (
  id bigserial primary key,
  run_id uuid not null references walk_forward_runs(id) on delete cascade,
  window_name text not null,
  train_start date not null,
  train_end date not null,
  test_start date not null,
  test_end date not null,
  selected_parameters jsonb not null,
  train_metrics jsonb not null,
  test_metrics jsonb not null,
  unique (run_id, window_name)
);

create index if not exists stress_test_results_run_idx on stress_test_results(run_id);
create index if not exists walk_forward_results_run_idx on walk_forward_results(run_id);
