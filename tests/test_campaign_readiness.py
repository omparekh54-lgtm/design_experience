from copy import deepcopy
from dataclasses import replace
from datetime import date,datetime,timedelta
import pytest

from packages.data_layer.cloud_sources import SourceError,IST
from packages.pms.cloud_collection import parse_meetings
from packages.pms.campaign_readiness import assess_readiness,connector_readiness_sql,qualifies_elapsed_session
from packages.pms.config import load_strategy_config
from test_cloud_collection import document,NOW

DAY=date(2026,10,21)


def test_announcements_received_after_collection_start_are_not_future():
    doc=replace(document([dict(bm_symbol='TEST',bm_date='22-Oct-2026',
        bm_timestamp='16-Oct-2026 19:03:00',bm_purpose='Financial Results')]),
        observed_at=NOW+timedelta(minutes=5))
    rows=parse_meetings(doc,NOW)
    assert rows[0]['announced_at']==NOW+timedelta(minutes=3)


def test_announcements_after_document_receipt_remain_blocked_even_if_wall_clock_later():
    doc=document([dict(bm_symbol='TEST',bm_date='22-Oct-2026',
        bm_timestamp='16-Oct-2026 19:03:00',bm_purpose='Financial Results')])
    with pytest.raises(SourceError,match='earnings_identity_or_timestamp_invalid'):
        parse_meetings(doc,NOW+timedelta(minutes=5))


def facts():
    return dict(safety=dict(execution_mode='PAPER',emergency_stop=False,locked_reason=None),
        active_instruments=100,missing_sectors=0,v2_reprocessing_pending=0,annual_cash_flow_companies=80,
        critical_dq_errors=0,benchmark_rows=4,price_instruments=100,period_identity_ready=True,
        latest_collection=dict(status='SUCCESS',details=dict(complete=True,
            raw_archive=dict(status='VERIFIED',anonymous_read_denied=True))))


PROOFS=dict(deployed_code_verified=True,scheduler_verified=True,rehearsal_verified=True)


def test_passing_checks_only_report_readiness_and_never_invent_activation():
    report=assess_readiness(facts(),load_strategy_config(),day=DAY,code_version='code',operational_proofs=PROOFS)
    assert report['status']=='READY_FOR_ACTIVATION'
    assert not report['campaign_activated'] and report['activated_at'] is None
    assert report['elapsed_campaign_sessions']==0


@pytest.mark.parametrize('change,blocker',[
    ({'missing_sectors':1},'REFERENCE_COVERAGE'),
    ({'v2_reprocessing_pending':1},'FINANCIAL_V2'),
    ({'benchmark_rows':3},'CURRENT_MARKET_DATA'),
    ({'latest_collection':None},'COMPLETE_COLLECTION'),
    ({'critical_dq_errors':1},'CRITICAL_DQ'),
    ({'period_identity_ready':False},'FINANCIAL_PERIOD_IDENTITY'),
    ({'safety':dict(execution_mode='PAPER',emergency_stop=False,locked_reason='reference_data_incomplete')},'SAFETY_UNLOCKED'),
])
def test_failed_readiness_gate_never_becomes_activation(change,blocker):
    payload=facts();payload.update(change)
    report=assess_readiness(payload,load_strategy_config(),day=DAY,code_version='code',operational_proofs=PROOFS)
    assert blocker in report['blockers'] and report['status']=='BLOCKED'
    assert not report['campaign_activated']


def test_unverified_operational_proofs_fail_closed():
    report=assess_readiness(facts(),load_strategy_config(),day=DAY,code_version='code')
    assert set(report['blockers'])=={'DEPLOYED_CODE','SCHEDULER_AND_ARCHIVE','RECOVERY_AND_REHEARSAL'}


def run():
    return dict(trade_date=str(DAY),started_at='2026-10-21T19:00:00+05:30',
        finished_at='2026-10-21T19:20:00+05:30',status='SUCCESS',mode='PAPER',config_hash='cfg',
        code_version='code',paper_execution_status='SUCCESS',collection_complete=True,reconciliation_ok=True)


def qualifies(row,activation='2026-10-21T08:00:00+05:30'):
    return qualifies_elapsed_session(row,activated_at=activation,config_hash='cfg',code_version='code',official_sessions={DAY})


def test_genuine_elapsed_exchange_session_qualifies():
    assert qualifies(run())


@pytest.mark.parametrize('field,value',[
    ('started_at','2026-10-22T19:00:00+05:30'),
    ('finished_at','2026-10-22T19:20:00+05:30'),
    ('started_at','2026-10-21T09:00:00+05:30'),
    ('started_at','2026-10-21T19:00:00'),
    ('status','FAILED'),('code_version','other'),('config_hash','other'),
    ('paper_execution_status','BLOCKED'),('collection_complete',False),('reconciliation_ok',False),
])
def test_backfilled_failed_unverified_or_drifted_journals_do_not_count(field,value):
    row=run();row[field]=value
    assert not qualifies(row)


def test_activation_after_session_open_cannot_claim_that_session():
    assert not qualifies(run(),'2026-10-21T12:00:00+05:30')
    assert not qualifies_elapsed_session(run(),activated_at='2026-10-20T08:00:00+05:30',
        config_hash='cfg',code_version='code',official_sessions=set())


def test_readiness_query_is_dated_and_has_no_writes():
    sql=connector_readiness_sql(DAY)
    assert '2026-10-21' in sql and ':stamp' not in sql
    assert not any(word in sql.lower() for word in ['update pms','delete from','insert into','alter table'])
