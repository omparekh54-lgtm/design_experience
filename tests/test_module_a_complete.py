from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from packages.pms.config import load_strategy_config
from packages.pms.fundamental_history import calculate_financial_history
from packages.pms.module_a_pipeline import ModuleAPipeline, evaluate_inputs
from packages.pms.module_a_context import IST, as_ist
from packages.pms.module_a_data import context_params, normalize_context

AS_OF = datetime(2026,10,18,18,tzinfo=IST)
FILED = datetime(2026,8,13,18,tzinfo=IST)


def policy():
    cfg = load_strategy_config()
    # Test policy, deliberately not an owner-confirmed production policy.
    cfg['fundamental']['leverage_limits']['default'] = {'max_debt_to_equity':1.0,'min_interest_coverage':3.0}
    return cfg


def history():
    periods = [date(2026,6,30),date(2026,3,31),date(2025,12,31),date(2025,9,30),
               date(2025,6,30),date(2025,3,31),date(2024,12,31),date(2024,9,30)]
    result = []
    for i, period in enumerate(periods):
        sale = [200,190,180,170,120,115,110,105][i]
        profit = [40,38,36,34,20,19,18,17][i]
        result.append({'period_end':period,'filed_at':FILED if i==0 else datetime.combine(period+timedelta(days=40),datetime.min.time(),tzinfo=IST),
                       'filing_kind':'quarter','consolidated':True,'source':'NSE','revision_number':1,
                       'facts':{'sales':sale,'operating_profit':profit*1.5,'pat':profit,'diluted_eps':profit/10,
                                'pbt':profit*1.3,'other_income':1,'exceptional_items':0,'debt':30,'equity':100,'interest_expense':2}})
    result.append({'period_end':date(2026,3,31),'filed_at':datetime(2026,5,15,tzinfo=IST),
                   'filing_kind':'annual','consolidated':True,'source':'NSE',
                   'facts':{'pat':100,'operating_cash_flow':100,'debt':30,'equity':100}})
    return result


def context(**kwargs):
    result = {'id':1,'instrument_id':'NSE:ABC','sector':'Information Technology','industry':'IT Services',
              'listing_known':True,'suspended':False,'price_date':date(2026,10,16),'liquidity_sessions':63,
              'shareholding_period':date(2026,6,30),'surveillance_known':True,'open_data_errors':0,
              'market_cap_cr':2500,'avg_traded_value_cr':10,'promoter_pledge_pct':0,'asm_gsm':False}
    return {**result,**kwargs}


def evaluate(h=None, c=None, cfg=None, stamp=AS_OF):
    return evaluate_inputs(history() if h is None else h,context() if c is None else c,stamp,policy() if cfg is None else cfg)


def test_complete_point_in_time_candidate_passes_all_stages():
    snapshot, decision = evaluate()
    assert decision.eligible
    assert decision.earnings_breakout
    assert decision.eligible_until == FILED+timedelta(days=100)
    assert decision.filing_timestamp == FILED
    assert sum(decision.components.values()) == decision.score
    assert decision.reason.startswith('Eligible:')


def test_stage_three_is_not_needed_for_membership():
    snapshot, decision = evaluate()
    from packages.pms.module_a import evaluate_fundamentals
    no_trigger = replace(snapshot,growth_acceleration_pp=0,margin_expansion_bps=0,
                         record_sales_8q=False,record_pat_8q=False,turnaround=False,triggers=())
    result = evaluate_fundamentals(no_trigger,policy())
    assert result.eligible
    assert not result.earnings_breakout


@pytest.mark.parametrize('sector',['Financial Services','BANKING','NBFC','Insurance'])
def test_financial_companies_are_excluded(sector):
    _, result = evaluate(c=context(sector=sector))
    assert not result.eligible
    assert 'financial_sector_excluded' in result.failed_rules


def test_expiry_at_exactly_100_days():
    deadline = FILED+timedelta(days=100)
    _, before = evaluate(stamp=deadline-timedelta(microseconds=1))
    _, at = evaluate(stamp=deadline)
    assert 'eligibility_expired_100_days' not in before.failed_rules
    assert 'eligibility_expired_100_days' in at.failed_rules


def test_failing_new_quarter_removes_eligibility_immediately():
    h = history()
    h.insert(0,{'period_end':date(2026,9,30),'filed_at':AS_OF,'filing_kind':'quarter','consolidated':True,
                'source':'NSE','facts':{**h[0]['facts'],'pat':-10}})
    _, old = evaluate(h=h,stamp=AS_OF-timedelta(microseconds=1))
    _, new = evaluate(h=h,stamp=AS_OF)
    assert old.eligible
    assert not new.eligible
    assert 'positive_pat' in new.failed_rules


def test_missing_quarter_does_not_shift_yoy_comparator():
    h = history()
    del h[2]
    metrics = calculate_financial_history(h,as_of=AS_OF)
    assert metrics.sales_yoy_pct == pytest.approx((200/120-1)*100)
    assert 'quarter_history_gaps' in metrics.quality_failures
    assert not evaluate(h=h)[1].eligible


def test_duplicate_revision_does_not_count_as_another_quarter():
    h = history()
    h.append(deepcopy(h[0]))
    assert calculate_financial_history(h,as_of=AS_OF).positive_growth_quarters_last_4 == 4
    assert evaluate(h=h)[1].eligible


def test_future_revision_is_not_used():
    h = history()
    revision = {**h[0],'filed_at':AS_OF+timedelta(seconds=1),'revision_number':2,
                'facts':{**h[0]['facts'],'pat':-999}}
    h.append(revision)
    assert evaluate(h=h)[1].eligible
    assert not evaluate(h=h,stamp=AS_OF+timedelta(seconds=1))[1].eligible


def test_other_income_cannot_manufacture_core_growth():
    h = history()
    h[0]['facts'].update(pbt=100,other_income=80,pat=80,diluted_eps=8)
    result = evaluate(h=h)[1]
    assert not result.eligible
    assert 'normalized_profit_growth' in result.failed_rules


def test_exceptional_income_is_removed_with_signed_adjustment():
    h = history()
    h[0]['facts'].update(pbt=100,exceptional_items=80,pat=80,diluted_eps=8)
    assert 'normalized_profit_growth' in evaluate(h=h)[1].failed_rules
    h[0]['facts'].update(pbt=30,exceptional_items=-30)
    assert calculate_financial_history(h,as_of=AS_OF).core_pbt_yoy_pct>100


def test_pbt_guard_catches_tax_driven_pat_increase():
    h = history();h[0]['facts'].update(pbt=20,pat=40,diluted_eps=4)
    result = evaluate(h=h)[1]
    assert 'pbt_not_growing' in result.failed_rules
    assert 'tax_rate_distortion_review' in result.flags


def test_unknown_income_lines_are_not_assumed_zero():
    h = history();del h[0]['facts']['exceptional_items']
    assert 'normalized_profit_growth_unavailable' in evaluate(h=h)[1].failed_rules


def test_missing_latest_annual_cash_flow_does_not_use_older_healthy_year():
    h = history();h[-1]['facts'].pop('operating_cash_flow')
    h.append({**h[-1],'period_end':date(2025,3,31),'facts':{'pat':100,'operating_cash_flow':200}})
    assert not evaluate(h=h)[1].eligible
    assert 'annual_ocf_to_pat_unavailable' in evaluate(h=h)[1].failed_rules


def test_mixed_statement_basis_cannot_pass_comparisons():
    h = history();h[4]['consolidated']=False
    result = evaluate(h=h)[1]
    assert not result.eligible
    assert 'mixed_reporting_basis' in result.failed_rules


def test_undefined_production_leverage_policy_fails_closed():
    result = evaluate(cfg=load_strategy_config())[1]
    assert not result.eligible
    assert 'sector_leverage_policy_unconfigured' in result.failed_rules


def test_sector_override_is_applied():
    cfg = policy();cfg['fundamental']['leverage_limits']['sectors']['Information Technology']={'max_debt_to_equity':0.2}
    assert 'debt_to_equity' in evaluate(cfg=cfg)[1].failed_rules


def test_missing_balance_sheet_is_not_eligible():
    h = history()
    for p in h:
        p['facts'].pop('debt',None);p['facts'].pop('equity',None)
    assert 'debt_to_equity_unavailable' in evaluate(h=h)[1].failed_rules


def test_interest_free_debt_free_company_has_defined_handling():
    h = history()
    for p in h:
        p['facts'].update(debt=0,interest_expense=0)
    assert evaluate(h=h)[1].eligible
    assert 'interest_free' in evaluate(h=h)[1].flags


@pytest.mark.parametrize('change,reason',[
    ({'surveillance_known':False},'surveillance_coverage_unverified'),
    ({'open_data_errors':1},'unresolved_data_quality_errors'),
    ({'sector':None},'sector_unavailable_as_of'),
    ({'liquidity_sessions':62},'liquidity_history_incomplete'),
    ({'price_date':date(2026,10,1)},'market_price_stale'),
    ({'shareholding_period':date(2025,12,31)},'shareholding_stale'),
    ({'promoter_pledge_pct':None},'promoter_pledge_unavailable'),
    ({'suspended':True},'security_suspended'),
])
def test_reference_gaps_and_suspension_fail_closed(change,reason):
    result = evaluate(c=context(**change))[1]
    assert not result.eligible
    assert reason in result.failed_rules


def test_missing_financials_returns_explainable_rejection_not_exception():
    snapshot,result = evaluate(h=[])
    assert not result.eligible
    assert 'quarterly_financial_history_unavailable' in result.failed_rules
    assert snapshot.filing_timestamp is None


def test_cyclical_valuation_is_soft_penalty_not_exclusion():
    cfg = policy();cfg['fundamental']['cyclical_high_pe_threshold']=10
    base = evaluate(c=context(sector='Metals & Mining'),cfg=policy())[1]
    penalized = evaluate(c=context(sector='Metals & Mining'),cfg=cfg)[1]
    assert penalized.eligible
    assert penalized.score == base.score-5
    assert 'high_valuation' in penalized.flags


def test_nan_facts_cannot_create_eligible_or_nan_score():
    h = history();h[0]['facts']['pat']=float('nan')
    snapshot,result = evaluate(h=h)
    assert not result.eligible
    import json
    json.dumps(ModuleAPipeline.serialize(snapshot,result),default=str,allow_nan=False)


def test_intraday_asof_never_reads_todays_close():
    assert context_params(datetime(2026,10,16,10,tzinfo=IST),policy())['market_date']==date(2026,10,15)
    assert context_params(datetime(2026,10,16,16,tzinfo=IST),policy())['market_date']==date(2026,10,16)


def test_naive_timestamps_are_explicitly_india_time():
    assert as_ist(datetime(2026,10,16,18)).utcoffset()==timedelta(hours=5,minutes=30)


def test_future_sector_observation_cannot_leak_into_backtest():
    row={'id':1,'trading_symbol':'ABC-EQ','nse_symbol':'ABC','current_sector':'IT','metadata':{
        'sector_classification':{'observed_at':(AS_OF+timedelta(days=1)).isoformat()}},
         'sector':None,'listed_from':date(2020,1,1),'surveillance_types_ready':2}
    assert normalize_context(row,AS_OF,policy())['sector'] is None


def test_raw_api_calculator_cannot_claim_warehouse_eligibility():
    from apps.api.main import app
    from dataclasses import asdict
    snap,_ = evaluate()
    payload = {k:asdict(snap)[k] for k in app.openapi()['components']['schemas']['FundamentalRequest']['properties']}
    response = TestClient(app).post('/v1/module-a/evaluate',json=payload)
    assert response.status_code==200
    assert response.json()['eligible'] is False
    assert response.json()['calculation_only'] is True


def test_saved_decision_is_idempotent_and_new_failure_overwrites_pass():
    # Use a real database engine/transaction; SQLite supports this upsert shape.
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('''create table module_a_history(instrument_id integer,as_of text,eligible boolean,score real,
        failed_rules text,triggers text,flags text,snapshot text,strategy_version text,unique(instrument_id,as_of,strategy_version))''')
    from sqlalchemy import event
    # SQLite has no JSONB type. Keep JSON storage semantics for this portability test.
    @event.listens_for(engine, 'before_cursor_execute', retval=True)
    def sqlite_json_cast(conn,cursor,statement,parameters,context,executemany):
        return statement.replace('cast(? as jsonb)','json(?)'),parameters
    pipeline = ModuleAPipeline(engine,policy())
    snapshot,passed = evaluate()
    pipeline.persist(1,AS_OF,snapshot,passed)
    failed_snapshot,failed = evaluate(c=context(asm_gsm=True))
    pipeline.persist(1,AS_OF,failed_snapshot,failed)
    with engine.connect() as conn:
        assert conn.execute(text('select count(*) from module_a_history')).scalar()==1
        assert conn.execute(text('select eligible from module_a_history')).scalar()==0
        import json
        payload = json.loads(conn.execute(text('select snapshot from module_a_history')).scalar())
        assert payload['decision']['eligible'] is False
        assert 'asm_gsm' in payload['decision']['failed_rules']


def test_new_annual_report_does_not_extend_quarter_membership():
    h = history();h[-1]['filed_at']=AS_OF
    snapshot,_ = evaluate(h=h)
    assert snapshot.eligible_until==FILED+timedelta(days=100)


def test_bad_policy_is_rejected_with_explicit_error():
    cfg=policy();cfg['fundamental']['leverage_limits']['default']['max_debt_to_equity']=float('nan')
    with pytest.raises(ValueError,match='Invalid leverage limit'):
        evaluate(cfg=cfg)


def test_saved_eligible_list_rechecks_current_hard_filters(monkeypatch):
    from sqlalchemy import event
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('create table instruments(id integer,exchange text,nse_symbol text,active boolean)')
        conn.exec_driver_sql("insert into instruments values(1,'NSE','ABC',1)")
        conn.exec_driver_sql('create table module_a_history(instrument_id integer,as_of text,eligible boolean,strategy_version text)')
        conn.execute(text('insert into module_a_history values(1,:stamp,1,:version)'),
                     {'stamp':str(AS_OF-timedelta(days=1)),'version':policy()['version']})
    # DISTINCT ON is PostgreSQL-specific; the fixture has one row per ID so its
    # equivalent SQLite DISTINCT lets us exercise real DB selection + revalidation.
    @event.listens_for(engine,'before_cursor_execute',retval=True)
    def portable_distinct(conn,cursor,statement,parameters,context,executemany):
        return statement.replace('distinct on (instrument_id)','distinct'),parameters
    pipeline=ModuleAPipeline(engine,policy())
    calls=[]
    def current_evaluation(instrument,stamp):
        calls.append((instrument,stamp))
        return evaluate(c=context(asm_gsm=True),stamp=stamp)
    monkeypatch.setattr(pipeline,'evaluate',current_evaluation)
    result=pipeline.eligible_list(AS_OF)
    assert calls==[('NSE:ABC',AS_OF)]
    assert result['eligible']==0
