from packages.pms.config import load_strategy_config
from packages.pms.module_a import FundamentalSnapshot, evaluate_fundamentals
from packages.pms.module_e import decide_regime
from packages.pms.risk import compute_stop, size_position


def eligible_snapshot(**overrides):
    data = dict(
        market_cap_cr=2500,
        avg_traded_value_cr=10,
        asm_gsm=False,
        promoter_pledge_pct=0,
        quarterly_sales_cr=200,
        pat_cr=25,
        sales_yoy_pct=25,
        operating_profit_yoy_pct=30,
        pat_yoy_pct=40,
        eps_yoy_pct=35,
        positive_growth_quarters_last_4=4,
        ttm_sales_rising=True,
        ttm_profit_rising=True,
        ocf_to_pat=1.0,
        quality_pass=True,
    )
    data.update(overrides)
    return FundamentalSnapshot(**data)


def test_module_a_passes_clean_candidate():
    decision = evaluate_fundamentals(eligible_snapshot(), load_strategy_config())
    assert decision.eligible is True
    assert decision.failed_rules == ()


def test_module_a_rejects_market_cap_at_or_below_1000cr():
    decision = evaluate_fundamentals(eligible_snapshot(market_cap_cr=1000), load_strategy_config())
    assert decision.eligible is False
    assert "market_cap" in decision.failed_rules


def test_module_a_rejects_asm_gsm():
    decision = evaluate_fundamentals(eligible_snapshot(asm_gsm=True), load_strategy_config())
    assert decision.eligible is False
    assert "asm_gsm" in decision.failed_rules


def test_stop_is_capped_to_5_5_percent_from_entry():
    stop = compute_stop(entry=500, signal_low=450, raw_stop_buffer_pct=0.5, max_stop_risk_pct=5.5)
    assert stop == 472.5


def test_position_respects_ten_percent_cap():
    result = size_position(
        capital=10_000_000,
        entry=500,
        signal_low=480,
        risk_per_trade_pct=0.5,
        max_position_pct=10,
        raw_stop_buffer_pct=0.5,
        max_stop_risk_pct=5.5,
    )
    assert result.position_value <= 1_000_000
    assert result.position_pct <= 10


def test_regime_hard_override_sets_capacity_zero():
    result = decide_regime(
        score=90,
        nifty_midcap_400_above_200dma=False,
        breadth_above_50dma_pct=25,
        cfg=load_strategy_config(),
    )
    assert result.max_open_trades == 0
    assert result.hard_override is True


def test_strong_regime_allows_seven_positions():
    result = decide_regime(
        score=85,
        nifty_midcap_400_above_200dma=True,
        breadth_above_50dma_pct=70,
        cfg=load_strategy_config(),
    )
    assert result.band == "STRONG"
    assert result.max_open_trades == 7
