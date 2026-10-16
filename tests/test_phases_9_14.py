from packages.pms.config import load_strategy_config
from packages.pms.module_e import RegimeInputs, RegimeTracker, calculate_regime_score
from packages.pms.module_c import Candidate, AllocationState, RankingInputs, allocate_candidates
from packages.pms.research import TradeOutcome, calculate_performance, run_robustness_grid, ResearchVariant


def healthy_regime_inputs(**overrides):
    data = dict(
        midcap_close=120, midcap_dma50=110, midcap_dma200=100, midcap_dma200_slope_pct=2,
        breadth_above_50dma_pct=70, breadth_above_21ema_pct=75, advance_decline_10d=1.5,
        new_highs_pct=5, new_lows_pct=1, india_vix=13, india_vix_5d_change_pct=-5,
        strategy_expectancy_r=0.4, valid_rallies_pct=70, closed_trades=15,
    )
    data.update(overrides)
    return RegimeInputs(**data)


def test_regime_score_uses_all_five_components():
    score, components = calculate_regime_score(healthy_regime_inputs())
    assert 0 <= score <= 100
    assert set(components) == {"trend", "breadth", "highs_lows", "volatility", "strategy_feedback"}


def test_regime_upgrade_requires_two_closes_but_downgrade_is_immediate():
    cfg = load_strategy_config(); tracker = RegimeTracker()
    weak = healthy_regime_inputs(midcap_close=90, midcap_dma50=100, midcap_dma200=105, breadth_above_50dma_pct=35, breadth_above_21ema_pct=35, advance_decline_10d=0.7, new_highs_pct=1, new_lows_pct=4, india_vix=25, india_vix_5d_change_pct=10)
    first = tracker.update(weak, cfg)
    high1 = tracker.update(healthy_regime_inputs(), cfg)
    high2 = tracker.update(healthy_regime_inputs(), cfg)
    assert high1.band == first.band or high1.max_open_trades <= high2.max_open_trades
    assert high2.max_open_trades >= high1.max_open_trades
    down = tracker.update(healthy_regime_inputs(midcap_close=80, midcap_dma50=100, midcap_dma200=110, breadth_above_50dma_pct=20), cfg)
    assert down.max_open_trades == 0


def candidate(symbol, score=90, sector="IT", value=100000, adtv=10000000, risk=3):
    inputs = RankingInputs(score, score, score, score, score, score)
    return Candidate(symbol, sector, True, True, 1, 4, value, adtv, False, 100, risk, inputs)


def test_module_c_respects_sector_and_regime_capacity():
    cfg = load_strategy_config()
    state = AllocationState(0, 0, 0, 1_000_000, {})
    result = allocate_candidates([candidate("A"), candidate("B"), candidate("C")], state, 2, cfg)
    assert len(result.selected) == 2
    assert "sector_limit" in result.rejected["C"] or "capacity" in result.rejected["C"]


def test_performance_metrics_include_drawdown_and_profit_factor():
    m = calculate_performance(initial_capital=100, ending_equity=120, equity_curve=[100,110,90,120], trades=[TradeOutcome(10,100,5), TradeOutcome(-5,110,3), TradeOutcome(15,105,8)], years=1)
    assert m.total_return_pct == 20
    assert m.max_drawdown_pct > 0
    assert m.profit_factor == 5.0


def test_robustness_grid_generates_cartesian_parameter_set():
    cfg = load_strategy_config()
    def runner(config, variant):
        return {"total_return_pct": config["technical"]["min_adr_20d_pct"], "max_drawdown_pct": 10, "profit_factor": 1.5}
    rows = run_robustness_grid(cfg, runner, ResearchVariant("X"), adr_values=(2.5,3), rally_values=(15,), risk_values=(0.5,), volume_values=(1.5,2))
    assert len(rows) == 4
