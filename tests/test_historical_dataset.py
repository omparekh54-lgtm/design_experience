from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json

import httpx
import polars as pl
import pytest

from packages.pms.config import load_strategy_config
from packages.pms.data_integrity import AuditScope
from packages.pms.historical_dataset import completion_plan, discover_financial_history, remote_audit_bundle, v2_financial_parser, write_completion_plan
from packages.pms.integrity_checks import CHECKS
from packages.pms import research_dataset as dataset
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, ExchangeFinancialsError

START, END = date(2018, 1, 1), date(2024, 12, 31)


@pytest.mark.parametrize('status', ['FAIL','ERROR','NOT_RUN','REVIEW_REQUIRED'])
def test_unresolved_or_unexecuted_evidence_never_means_complete(status):
    report = dict(run_id='audit',summary=dict(complete=True),checks=[dict(
        code='MISSING',group='COVERAGE',status=status,finding_count=None,
        remediation='Obtain original vintage',samples=[])])
    plan = completion_plan(report)
    assert plan['status']=='BLOCKED' and not plan['dataset_complete']
    assert plan['tasks'][0]['finding_count'] is None
    assert not plan['tasks'][0]['automatic_source_write']


def test_repair_plan_prioritizes_identity_before_financial_backfill_and_retains_scope(tmp_path):
    report=dict(run_id='audit',requested_scope=dict(start=str(START),end=str(END)),
        summary=dict(complete=False),checks=[dict(code=c,group=g,status='FAIL',finding_count=n,
        samples=[],remediation='Source review') for c,g,n in [('FIN','FUNDAMENTALS',12),('MASTER','UNIVERSE',1)]])
    plan=write_completion_plan(report,tmp_path/'repairs.json')
    assert [t['check'] for t in plan['tasks']]==['MASTER','FIN']
    assert json.loads((tmp_path/'repairs.json').read_text())==plan


def test_remote_bundle_is_one_read_only_transaction_with_explicit_unrun_checks():
    bundle=remote_audit_bundle(AuditScope(START,END),load_strategy_config())
    assert bundle['statements'][0]=='SET TRANSACTION READ ONLY'
    assert len(bundle['check_codes'])==44
    assert bundle['not_run']==['SCHEMA_CONTRACT','A_DECISION_KNOWABILITY']
    assert not bundle['dataset_complete']
    for sql in bundle['statements']:
        assert not any(word in sql.lower() for word in ('insert into','delete from','update pms','alter table'))
        assert ':start' not in sql and ':ids' not in sql
    quarter=next(sql for sql in bundle['statements'] if "'FINANCIAL_QUARTER_COVERAGE' code" in sql)
    assert "interval '120 days'" in quarter and "interval '2 years'" in quarter
    assert 'delisted_on' in quarter and 'active=true' not in quarter


def record(key):
    return SimpleNamespace(symbol='TEST',period_end=END,filed_at=datetime(2025,2,1,tzinfo=timezone.utc),
        statement_scope='consolidated',submission_type='Original',xbrl_uri='https://source/'+str(key),external_id=str(key))


def test_discovery_traverses_pages_preserves_vintages_and_stops_on_short_page():
    calls=[]
    pages=[[record(1),record(2)],[record(3)]]
    def fetch(**kw):
        calls.append(kw);return pages[kw['page']-1]
    rows=discover_financial_history(SimpleNamespace(list_filings=fetch),'TEST',START,END,page_size=2)
    assert [r.external_id for r in rows]==['1','2','3']
    assert [c['page'] for c in calls]==[1,2] and all(c['strict'] for c in calls)


def test_repeated_source_page_and_page_cap_cannot_be_reported_as_complete():
    provider=SimpleNamespace(list_filings=lambda **kw:[record(1)])
    with pytest.raises(ValueError,match='repeated'):
        discover_financial_history(provider,'TEST',START,END,page_size=1)
    with pytest.raises(ValueError,match='page cap'):
        discover_financial_history(provider,'TEST',START,END,page_size=1,max_pages=1)


def test_strict_discovery_does_not_drop_malformed_source_rows():
    def handler(request):
        return httpx.Response(200,json=[{'symbol':'TEST','period_end':'bad'}])
    provider=NSEIntegratedFinancialsProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ExchangeFinancialsError,match='unparseable'):
        provider.list_filings(symbol='TEST',strict=True)
    assert provider.list_filings(symbol='TEST')==[]


def test_reprocessing_refuses_old_parser(monkeypatch):
    from packages.data_layer import xbrl
    monkeypatch.setattr(xbrl,'EXTRACTION_VERSION','OLD',raising=False)
    with pytest.raises(RuntimeError,match='migration 012'):
        v2_financial_parser()


def test_export_contains_all_isolated_inputs_and_uses_one_snapshot(monkeypatch,tmp_path):
    calls=[]
    class Snapshot:
        def execute(self,sql,params):
            calls.append((str(sql),params))
            # Heterogeneous source metadata must be serialized losslessly in Parquet.
            return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[dict(id=1,raw_payload={'source':{'vintage':'original'}})]))
    entries=[]
    @contextmanager
    def snapshot(engine):
        entries.append('start');yield Snapshot();entries.append('end')
    monkeypatch.setattr(dataset,'consistent_inputs',snapshot)
    manifest=dataset.export_research_dataset(object(),tmp_path,START,END)
    assert entries==['start','end']
    assert set(manifest.tables)>={'instruments','module_e_benchmark_bars','sector_history',
        'shares_outstanding_history','provider_snapshots','financial_filings','financial_facts','data_quality_issues'}
    exported=json.loads((Path(manifest.root_path)/'manifest.json').read_text())
    assert exported['snapshot_consistency']=='REPEATABLE_READ_READ_ONLY'
    assert not exported['dataset_complete'] and exported['acceptance']=='NOT_CERTIFIED'
    raw=pl.read_parquet(Path(manifest.root_path)/'provider_snapshots.parquet')['raw_payload'][0]
    assert json.loads(raw)=={'source':{'vintage':'original'}}
    filings=dataset.EXPORTS['financial_filings'];facts=dataset.EXPORTS['financial_facts']
    assert 'filing_timestamp<=:stamp' in filings and 'filing_timestamp<=:stamp' in facts
    assert '1100 days' in dataset.EXPORTS['daily_prices']
    assert all(p['stamp'].tzinfo is not None for _,p in calls)


def test_invalid_export_dates_do_not_create_output_files(tmp_path):
    with pytest.raises(ValueError,match='historical dates'):
        dataset.export_research_dataset(object(),tmp_path/'new',END,START)
    assert not (tmp_path/'new').exists()


def test_filing_duplicate_identity_separates_annual_and_quarter():
    sql=next(c.sql for c in CHECKS if c.code=='FILINGS_DUPLICATES')
    assert 'filing_kind' in sql


@pytest.mark.parametrize('keys,ready', [
    ([['instrument_id','period_end','filing_timestamp','source','consolidated','revision_number']],False),
    ([['instrument_id','period_end','filing_timestamp','source','consolidated','revision_number','filing_kind']],True),
    ([],False),
])
def test_financial_writes_require_period_aware_unique_identity(keys,ready):
    from packages.pms.historical_dataset import require_financial_period_identity
    class Engine:
        def connect(self):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:keys))
    if ready:require_financial_period_identity(Engine())
    else:
        with pytest.raises(RuntimeError,match='migration 012'):
            require_financial_period_identity(Engine())
