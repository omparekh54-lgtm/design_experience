alter table audit_log add column if not exists correlation_id uuid;
alter table audit_log add column if not exists entity_type text;
alter table audit_log add column if not exists entity_id text;
alter table audit_log add column if not exists strategy_version text;
alter table audit_log add column if not exists data_version text;
alter table audit_log add column if not exists before_state jsonb;
alter table audit_log add column if not exists after_state jsonb;

create index if not exists audit_log_entity_idx on audit_log(entity_type, entity_id, created_at desc);
create index if not exists audit_log_correlation_idx on audit_log(correlation_id, created_at);

create table if not exists paper_validation_runs (
  id uuid primary key,
  started_on date not null,
  ended_on date not null,
  account_mode text not null default 'PAPER',
  criteria jsonb not null,
  metrics jsonb not null,
  gates jsonb not null,
  passed boolean not null,
  created_at timestamptz not null default now(),
  created_by text not null default 'system'
);

create table if not exists live_validation_sessions (
  id uuid primary key,
  status text not null default 'DRAFT',
  started_at timestamptz,
  ended_at timestamptz,
  max_capital numeric not null,
  max_order_value numeric not null,
  max_open_positions integer not null,
  max_daily_loss numeric not null,
  allowed_symbols jsonb not null default '[]'::jsonb,
  notes text,
  created_at timestamptz not null default now(),
  created_by text not null,
  activated_by text,
  closed_by text
);

create table if not exists live_order_approvals (
  id uuid primary key,
  session_id uuid not null references live_validation_sessions(id),
  instrument_id bigint not null references instruments(id),
  side text not null,
  quantity integer not null,
  order_type text not null,
  limit_price numeric,
  trigger_price numeric,
  estimated_order_value numeric not null,
  status text not null default 'PROPOSED',
  proposal_reason text,
  proposed_at timestamptz not null default now(),
  proposed_by text not null default 'strategy',
  approved_at timestamptz,
  approved_by text,
  submitted_order_id uuid references broker_orders(id),
  rejection_reason text
);

create table if not exists production_readiness (
  singleton boolean primary key default true check (singleton),
  stage text not null default 'PAPER',
  readiness_status text not null default 'NOT_READY',
  gates jsonb not null default '{}'::jsonb,
  latest_paper_validation_id uuid references paper_validation_runs(id),
  latest_live_validation_id uuid references live_validation_sessions(id),
  updated_at timestamptz not null default now(),
  updated_by text not null default 'system'
);

insert into production_readiness(singleton, stage, readiness_status, updated_by)
values (true, 'PAPER', 'NOT_READY', 'migration')
on conflict (singleton) do nothing;

create index if not exists paper_validation_runs_date_idx on paper_validation_runs(created_at desc);
create index if not exists live_validation_sessions_status_idx on live_validation_sessions(status, created_at desc);
create index if not exists live_order_approvals_status_idx on live_order_approvals(status, proposed_at desc);

create or replace function audit_operational_change() returns trigger as $$
declare
  before_json jsonb;
  after_json jsonb;
  entity text;
begin
  if tg_op = 'INSERT' then
    before_json := null;
    after_json := to_jsonb(new);
  elsif tg_op = 'UPDATE' then
    before_json := to_jsonb(old);
    after_json := to_jsonb(new);
  else
    before_json := to_jsonb(old);
    after_json := null;
  end if;
  entity := coalesce(after_json->>'id', before_json->>'id', after_json->>'singleton', before_json->>'singleton');
  insert into audit_log(event_type,actor,payload,entity_type,entity_id,before_state,after_state)
  values (upper(tg_table_name)||'_'||tg_op,'db_trigger',jsonb_build_object('operation',tg_op),tg_table_name,entity,before_json,after_json);
  return coalesce(new, old);
end;
$$ language plpgsql;

do $$
declare t text;
begin
  foreach t in array array[
    'broker_orders','broker_fills','portfolio_ledger','portfolio_snapshots',
    'reconciliation_runs','worker_runs','safety_state','paper_validation_runs',
    'live_validation_sessions','live_order_approvals','production_readiness'
  ] loop
    execute format('drop trigger if exists audit_%I on %I', t, t);
    execute format('create trigger audit_%I after insert or update or delete on %I for each row execute function audit_operational_change()', t, t);
  end loop;
end $$;
