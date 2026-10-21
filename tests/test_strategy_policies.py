"""Policy thresholds, boundaries, provenance and retained data gates."""
from copy import deepcopy
from dataclasses import replace

import pytest

from packages.pms.config import load_strategy_config
from packages.pms.module_a_context import validate_module_a_config
from packages.pms.module_a_pipeline import evaluate_inputs
from packages.pms.module_a import evaluate_fundamentals
from packages.pms.module_e_pipeline import ModuleEPipeline
from packages.pms.policies import policy_summary, resolve_leverage_policy, validate_policy_config, is_cyclical
from test_module_a_complete import history, context, AS_OF


def evaluate(sector, debt_ratio, coverage, *, cfg=None, market_cap=2500):
    rows = deepcopy(history())
    for row in rows:
        row['facts']['debt'] = debt_ratio * 100
        row['facts']['equity'] = 100
        if 'operating_profit' in row['facts']:
            row['facts']['interest_expense'] = row['facts']['operating_profit'] / coverage
    return evaluate_inputs(rows,context(sector=sector,market_cap_cr=market_cap),AS_OF,cfg or load_strategy_config())


@pytest.mark.parametrize('sector,debt,cover', [
    ('Information Technology',.5,5), ('Fast Moving Consumer Goods',.5,5), ('Healthcare',.5,5),
    ('Construction Materials',1.5,3), ('Construction',1.5,3), ('Metals & Mining',1.5,3),
    ('Oil Gas & Consumable Fuels',1.5,3), ('Power',2,2.5), ('Telecommunication',2,2.5),
    ('Realty',1.5,2.5), ('Other Nonfinancial Sector',1,3),
])
def test_sector_policy_exact_boundaries_pass_and_violations_fail(sector,debt,cover):
    snapshot, decision = evaluate(sector,debt,cover)
    assert decision.eligible
    applied = snapshot.reference_context['applied_policy']
    assert applied['leverage']['limits'] == {'max_debt_to_equity':debt,'min_interest_coverage':cover}
    assert applied['status'] == 'PROVISIONAL_BACKTEST'
    assert 'provisional_policy' in decision.flags
    assert 'debt_to_equity' in evaluate(sector,debt+.01,cover)[1].failed_rules
    assert 'interest_coverage' in evaluate(sector,debt,cover-.01)[1].failed_rules


@pytest.mark.parametrize('alias,canonical', [
    (' IT ','Information Technology'),('information   TECHNOLOGY','Information Technology'),
    ('FMCG','Fast Moving Consumer Goods'),('Pharmaceuticals','Healthcare'),
    ('Metals','Metals & Mining'),('Metals and Mining','Metals & Mining'),
    ('Oil & Gas','Oil Gas & Consumable Fuels'),('Telecommunications','Telecommunication'),
    ('Real Estate','Realty'),
])
def test_sector_aliases_case_spacing_and_punctuation_are_consistent(alias,canonical):
    cfg=load_strategy_config()
    assert resolve_leverage_policy(cfg,alias) == resolve_leverage_policy(cfg,canonical)


def test_default_policy_does_not_make_missing_financial_data_eligible():
    rows=history()
    for row in rows:
        row['facts'].pop('debt',None)
        row['facts'].pop('equity',None)
    _,d=evaluate_inputs(rows,context(),AS_OF,load_strategy_config())
    assert not d.eligible and 'debt_to_equity_unavailable' in d.failed_rules
    assert 'sector_leverage_policy_unconfigured' not in d.failed_rules


@pytest.mark.parametrize('sector',['Banks','Financial Services','NBFC','Insurance','BANKING'])
def test_financial_companies_remain_excluded_with_configured_defaults(sector):
    assert 'financial_sector_excluded' in evaluate(sector,.1,10)[1].failed_rules


def test_cyclical_pe_penalty_is_soft_and_strictly_above_threshold():
    snapshot,base=evaluate('Metals & Mining',.3,5)
    at=evaluate_fundamentals(replace(snapshot,valuation_pe=25),load_strategy_config())
    above=evaluate_fundamentals(replace(snapshot,valuation_pe=25.01),load_strategy_config())
    assert at.eligible and above.eligible
    assert 'high_valuation' not in at.flags
    assert above.score == at.score-5 and 'high_valuation' in above.flags
    other=evaluate_fundamentals(replace(snapshot,cyclical=False,valuation_pe=50),load_strategy_config())
    assert 'high_valuation' not in other.flags


@pytest.mark.parametrize('sector,industry',[('Oil & Gas','Refining'),('Metals & Mining','Steel'),('Consumer Goods','Sugar')])
def test_cyclical_classification_handles_punctuation_and_industry(sector,industry):
    assert is_cyclical(sector,industry)


def test_policy_fingerprint_and_strategy_version_prevent_silent_reuse():
    cfg=load_strategy_config()
    a=policy_summary(cfg,'Power')
    assert a['version']=='FUNDAMENTAL_POLICY_R1'
    assert cfg['version']=='PMS_v1.2.0_policy_r1'
    assert a['fingerprint']==policy_summary(deepcopy(cfg),'Power')['fingerprint']
    cfg['fundamental']['leverage_limits']['sectors']['Power']['max_debt_to_equity']=1.8
    assert a['fingerprint'] != policy_summary(cfg,'Power')['fingerprint']
    assert cfg['fundamental']['legacy_surveillance_verified'] is False


@pytest.mark.parametrize('value',[True,'1',-1,float('nan'),float('inf')])
def test_bad_leverage_values_are_rejected_before_scanning(value):
    cfg=load_strategy_config()
    cfg['fundamental']['leverage_limits']['default']['max_debt_to_equity']=value
    with pytest.raises(ValueError,match='Invalid leverage limit'):
        validate_module_a_config(cfg)


@pytest.mark.parametrize('field,value',[('cyclical_high_pe_threshold',True),('cyclical_high_pe_threshold',0),
    ('cyclical_valuation_penalty_points',-1),('cyclical_valuation_penalty_points',101),
    ('cyclical_valuation_penalty_points',float('nan')),('policy_status','APPROVED_BY_DEFAULT')])
def test_invalid_valuation_and_policy_status_cannot_silently_apply(field,value):
    cfg=load_strategy_config();cfg['fundamental'][field]=value
    with pytest.raises(ValueError):validate_policy_config(cfg)


def test_unknown_benchmark_never_silently_uses_another_index():
    cfg=load_strategy_config()
    p=ModuleEPipeline(None,cfg)
    assert p.MIDSMALL_CODE==cfg['regime']['benchmark_code']=='NIFTYMIDSMALLCAP400'
    cfg['regime']['benchmark_code']='NIFTY500'
    with pytest.raises(ValueError,match='Unsupported regime benchmark'):ModuleEPipeline(None,cfg)


def test_ambiguous_sector_keys_or_dangling_aliases_fail_validation():
    cfg=load_strategy_config()
    cfg['fundamental']['leverage_limits']['sectors']['information technology']={}
    with pytest.raises(ValueError,match='Duplicate'):validate_policy_config(cfg)
    cfg=load_strategy_config()
    cfg['fundamental']['leverage_limits']['sector_aliases']['UNKNOWN']='Missing Sector'
    with pytest.raises(ValueError,match='Invalid'):validate_policy_config(cfg)


def test_policy_inspection_is_offline_and_discloses_provisional_values(capsys):
    import json
    from scripts.show_strategy_policy import main
    assert main(['--sector','Power']) == 0
    result=json.loads(capsys.readouterr().out)
    assert result['policy']['leverage']['limits']['max_debt_to_equity']==2
    assert result['policy']['status']=='PROVISIONAL_BACKTEST'
    assert result['performance_validated'] is False
