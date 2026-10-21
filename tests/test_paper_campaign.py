from copy import deepcopy
from datetime import datetime,timedelta,date
from uuid import uuid4
import pytest
from packages.pms.paper_campaign import operational_proofs,draft_manifest,campaign_evidence
from packages.pms.config import load_strategy_config
from packages.pms.module_a_context import IST
from scripts.verify_paper_operations import scheduled_delivery,completed_session
from packages.pms.market_calendar import MarketCalendar

NOW=datetime(2026,10,22,19,tzinfo=IST)

def test_draft_cannot_claim_activation_or_elapsed_evidence():
    result=draft_manifest({'blockers':['FINANCIAL_V2']},load_strategy_config(),[{'id':1,'isin':'X'}],code_version='code')
    assert result['status']=='PREPARED' and result['activated_at'] is None
    assert result['elapsed_campaign_sessions']==0 and not result['campaign_activated']
    assert result['blockers']==['FINANCIAL_V2']


def receipt():
    return dict(version='PAPER_OPERATIONS_v1',checked_at=NOW.isoformat(),code_version='code',
                checks=dict(deployed_code=True,scheduled_delivery=True,archive=True,restore=True,read_only_rehearsal=True))

@pytest.mark.parametrize('change',[{'checked_at':(NOW-timedelta(days=2)).isoformat()}, {'checked_at':(NOW+timedelta(seconds=1)).isoformat()}, {'code_version':'other'},{'version':'other'}])
def test_stale_future_or_changed_operational_receipt_cannot_pass(change):
    r=receipt();r.update(change)
    assert not any(operational_proofs(r,now=NOW,code_version='code').values())


def test_operation_checks_derive_from_results_and_missing_proofs_block():
    assert all(operational_proofs(receipt(),now=NOW,code_version='code').values())
    r=receipt();r['checks']['archive']=False
    assert not operational_proofs(r,now=NOW,code_version='code')['scheduler_verified']
    assert not any(operational_proofs({},now=NOW,code_version='code').values())


def records():
    cid=str(uuid4());activation=NOW.replace(hour=8)
    c=dict(id=cid,activated_at=activation,manifest=dict(config_hash='cfg',code_version='code'))
    details=dict(campaign_id=cid,campaign_guard_ok=True,trade_date='2026-10-22',mode='PAPER',
                 config_hash='cfg',code_version='code',paper_execution_status='SUCCESS',collection_complete=True,reconciliation_ok=True)
    r=dict(id=uuid4(),status='SUCCESS',started_at=NOW.replace(hour=16),finished_at=NOW.replace(hour=17),details=details)
    return c,r


def test_actual_journal_clocks_and_distinct_dates_bound_evidence():
    c,r=records();r['details']['started_at']='2000-01-01'
    result=campaign_evidence(c,[r,r],{date(2026,10,22)},now=NOW)
    assert result['elapsed_campaign_sessions']==1
    assert not result['duration_gate_passed'] and not result['final_acceptance']

@pytest.mark.parametrize('field,value',[('campaign_id','other'),('campaign_guard_ok',False),('code_version','other'),('collection_complete',False),('paper_execution_status','BLOCKED')])
def test_wrong_or_failed_campaign_journals_do_not_count(field,value):
    c,r=records();r['details'][field]=value
    assert campaign_evidence(c,[r],{date(2026,10,22)},now=NOW)['elapsed_campaign_sessions']==0


def test_future_finish_and_activation_after_open_do_not_count():
    c,r=records();r['finished_at']=NOW+timedelta(seconds=1)
    assert campaign_evidence(c,[r],{date(2026,10,22)},now=NOW)['elapsed_campaign_sessions']==0
    c,r=records();c['activated_at']=NOW.replace(hour=12)
    assert campaign_evidence(c,[r],{date(2026,10,22)},now=NOW)['elapsed_campaign_sessions']==0


def test_queued_or_manual_run_is_not_scheduled_delivery_evidence():
    base=dict(id=1,event='schedule',head_branch='main',status='completed',conclusion='failure',created_at=NOW.isoformat())
    assert scheduled_delivery([base],NOW)==1 # Delivery is distinct from collection success.
    for changes in [dict(status='pending'),dict(event='workflow_dispatch'),dict(head_branch='other')]:
        assert scheduled_delivery([{**base,**changes}],NOW) is None


def test_session_selection_does_not_request_open_or_holiday_session():
    cal=MarketCalendar(holiday_years={2026:{date(2026,10,20)}})
    assert completed_session(cal,NOW.replace(day=21,hour=8))==date(2026,10,19)
