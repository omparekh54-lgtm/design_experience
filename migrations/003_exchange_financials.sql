alter table financial_filings add column if not exists submission_type text;
alter table financial_filings add column if not exists audit_status text;
alter table financial_filings add column if not exists revised_at timestamptz;
alter table financial_filings add column if not exists revision_remarks text;
alter table financial_filings add column if not exists exchange_received_at timestamptz;
alter table financial_filings add column if not exists exchange_disseminated_at timestamptz;
alter table financial_filings add column if not exists filing_kind text not null default 'quarter';
alter table financial_filings add column if not exists external_id text;
alter table financial_filings add column if not exists details_uri text;
alter table financial_filings add column if not exists xbrl_uri text;
alter table financial_filings add column if not exists raw_payload jsonb not null default '{}'::jsonb;

drop index if exists financial_filings_identity_uq;
create unique index if not exists financial_filings_identity_v2_uq
  on financial_filings (
    instrument_id,
    period_end,
    filing_timestamp,
    source,
    consolidated,
    revision_number
  );

create index if not exists financial_filings_period_scope_idx
  on financial_filings (instrument_id, period_end desc, consolidated, filing_timestamp desc);

create index if not exists financial_filings_external_id_idx
  on financial_filings (source, external_id)
  where external_id is not null;
