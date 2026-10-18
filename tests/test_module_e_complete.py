from copy import deepcopy
from dataclasses import replace, asdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import json
import pandas as pd
import pytest

from packages.pms.config import load_strategy_config
from packages.pms.module_a_context import IST
from packages.pms.market_calendar import MarketCalendar
from packages.pms.module_e import *
from packages.pms.module_e_data import classify_liquidity, verified_breadth
from packages.pms.module_e_pipeline import (
    ModuleEPipeline,
    strategy_feedback,
    compact_quality,
)

CFG = load_strategy_config()
D = date(2026, 10, 16)


def healthy(**kw):
    values = dict(
        midcap_close=120,
        midcap_dma50=110,
        midcap_dma200=100,
        midcap_dma200_slope_pct=2,
        breadth_above_50dma_pct=70,
        breadth_above_21ema_pct=75,
        advance_decline_10d=1.5,
        new_highs_pct=5,
        new_lows_pct=1,
        india_vix=13,
        india_vix_5d_change_pct=-5,
        strategy_expectancy_r=0.4,
        valid_rallies_pct=70,
        closed_trades=15,
    )
    return RegimeInputs(**{**values, **kw})


@pytest.mark.parametrize(
    "score,band,cap",
    [
        (34.99, "DEFENSIVE", 0),
        (35, "WEAK", 2),
        (50, "NEUTRAL", 4),
        (65, "HEALTHY", 6),
        (80, "STRONG", 7),
    ],
)
def test_capacity_thresholds(score, band, cap):
    d = decide_regime(score, True, 70, CFG)
    assert d.band == band and d.max_open_trades == cap


def test_all_components_and_new_highs_trend_are_scored():
    score, components = calculate_regime_score(healthy())
    assert sum(components.values()) == score and len(components) == 5
    assert calculate_regime_score(healthy(net_highs_trend_pp=3))[0] > score
    assert calculate_regime_score(healthy(net_highs_trend_pp=-3))[0] < score


@pytest.mark.parametrize(
    "change",
    [
        {"india_vix": float("nan")},
        {"new_lows_pct": 101},
        {"midcap_close": 0},
        {"advance_decline_10d": -1},
        {"closed_trades": 10, "strategy_expectancy_r": None},
        {"closed_trades": 10, "valid_rallies_pct": None},
    ],
)
def test_invalid_regime_inputs_rejected_before_tracker_mutation(change):
    t = RegimeTracker()
    before = t.checkpoint()
    with pytest.raises(ValueError):
        t.update(healthy(**change), CFG)
    assert t.checkpoint() == before


def test_feedback_neutral_until_ten_and_uses_actual_inputs_afterward():
    _, under = calculate_regime_score(
        healthy(closed_trades=9, strategy_expectancy_r=None, valid_rallies_pct=None)
    )
    _, after = calculate_regime_score(healthy(closed_trades=10))
    assert under["strategy_feedback"] == 7.5 and after["strategy_feedback"] > 7.5


def test_override_strict_below_and_breadth_boundary():
    t = RegimeTracker()
    at = t.update(healthy(midcap_close=100, breadth_above_50dma_pct=29), CFG)
    assert not at.hard_override
    below = t.update(healthy(midcap_close=99, breadth_above_50dma_pct=29), CFG)
    assert below.hard_override and below.max_open_trades == 0
    assert not decide_regime(90, False, 30, CFG).hard_override


def test_tracker_session_replay_conflict_and_checkpoint_roundtrip():
    t = RegimeTracker()
    first = t.update(healthy(), CFG, session=D)
    checkpoint = t.checkpoint()
    restored = RegimeTracker.restore(json.loads(json.dumps(checkpoint)))
    assert (
        restored.update(healthy(), CFG, session=D) == first
        and restored.checkpoint() == checkpoint
    )
    with pytest.raises(ValueError):
        restored.update(healthy(india_vix=14), CFG, session=D)
    with pytest.raises(ValueError):
        restored.update(healthy(), CFG, session=D - timedelta(days=1))


def test_three_score_smoothing_immediate_downgrade_and_two_close_upgrade(monkeypatch):
    import packages.pms.module_e as m

    monkeypatch.setattr(
        m,
        "calculate_regime_score",
        lambda i: (i.midcap_close, {"fixture": i.midcap_close}),
    )
    t = RegimeTracker()
    for day, score in enumerate([40, 40, 40], 1):
        t.update(healthy(midcap_close=score), CFG, session=date(2026, 10, day))
    assert t.current.band == "WEAK"
    t.update(healthy(midcap_close=90), CFG, session=date(2026, 10, 4))
    assert t.current.band == "WEAK" and t.upgrade_closes == 1
    # Changed target band resets exact-band confirmation.
    t.update(healthy(midcap_close=90), CFG, session=date(2026, 10, 5))
    assert t.current.band == "WEAK"
    t.update(healthy(midcap_close=90), CFG, session=date(2026, 10, 6))
    t.update(healthy(midcap_close=90), CFG, session=date(2026, 10, 7))
    assert t.current.band == "STRONG"
    t.update(healthy(midcap_close=0), CFG, session=date(2026, 10, 8))
    assert t.current.band == "NEUTRAL"  # average (90+90+0)/3=60, downgrade now


def test_cold_start_holds_zero_until_full_window_and_upgrade_confirmation():
    t = RegimeTracker()
    for i in range(3):
        result = t.update(
            healthy(), CFG, session=D + timedelta(days=i), require_full_window=True
        )
        assert result.max_open_trades == 0
    assert (
        t.update(
            healthy(), CFG, session=D + timedelta(days=3), require_full_window=True
        ).max_open_trades
        == 7
    )


def test_corrupt_checkpoint_cannot_restore_excess_capacity():
    t = RegimeTracker()
    t.update(healthy(), CFG, session=D)
    p = t.checkpoint()
    p["current"]["band"] = "WEAK"
    p["current"]["max_open_trades"] = 7
    with pytest.raises(ValueError):
        RegimeTracker.restore(p)


def test_liquidity_boundary_and_incomplete_nonliquid_are_explicit():
    rows = [
        dict(
            id=1,
            instrument_id="NSE:A",
            sessions=20,
            latest=D,
            adtv=10000000,
            invalid_rows=0,
            immature=False,
        ),
        dict(
            id=2,
            instrument_id="NSE:B",
            sessions=19,
            latest=D,
            adtv=1,
            invalid_rows=0,
            immature=False,
        ),
        dict(
            id=3,
            instrument_id="NSE:IPO",
            sessions=2,
            latest=D,
            adtv=1,
            invalid_rows=0,
            immature=True,
        ),
    ]
    liquid, q = classify_liquidity(rows, D)
    assert (
        len(liquid) == 1
        and q["unclassified"] == 1
        and q["immature_listings_excluded"] == 1
    )
    assert q["independent_of_module_a"]


def test_breadth_uses_daily_advance_decline_observations_not_ten_day_net():
    dates = list(pd.bdate_range(end=D, periods=300).date)
    closes = [100.0] * (300 - 11) + [
        100,
        103,
        102,
        105,
        104,
        107,
        106,
        109,
        108,
        111,
        110,
    ]
    rows = [
        dict(
            instrument_id=1,
            trade_date=d,
            open=c,
            high=c + 2,
            low=c - 2,
            close=c,
            volume=1000000,
            turnover=None,
            source="nse",
        )
        for d, c in zip(dates, closes)
    ]
    measurements, q = verified_breadth(
        [{"id": 1, "instrument_id": "NSE:A", "listed_from": None}],
        rows,
        [],
        MarketCalendar(dates),
        D,
        CFG,
    )
    assert measurements["advance_decline_10d"] == 1
    assert q["advance_observations"] == 5 and q["decline_observations"] == 5
    assert measurements["breadth_above_50dma_pct"] == 100


def test_breadth_does_not_silently_drop_a_liquid_stock_with_missing_history():
    dates = list(pd.bdate_range(end=D, periods=300).date)
    values, q = verified_breadth(
        [{"id": 1, "instrument_id": "NSE:A", "listed_from": None}],
        [],
        [],
        MarketCalendar(dates),
        D,
        CFG,
    )
    assert values is None and q["expected_liquid_stocks"] == 1 and q["exceptions"]


def feedback_rows(count=10, **kw):
    return [
        dict(
            account_modes=["PAPER"],
            realized_pnl=1100,
            fees=100,
            initial_fill={"entry": 100, "stop": 95, "shares": 1000},
            **kw
        )
        for _ in range(count)
    ]


def test_feedback_is_net_r_not_arbitrary_rupee_scaling():
    result = strategy_feedback(feedback_rows())
    assert result["expectancy_r"] == pytest.approx(0.2) and not result["reasons"]
    assert strategy_feedback(feedback_rows(9))["expectancy_r"] is None


def test_feedback_never_infers_original_risk_from_moved_stop():
    rows = feedback_rows()
    rows[0]["initial_fill"] = None
    assert (
        "strategy_initial_risk_or_pnl_unavailable" in strategy_feedback(rows)["reasons"]
    )


def test_feedback_limits_last_fifteen_and_excludes_sandbox_and_live():
    rows = feedback_rows(16)
    rows[-1]["realized_pnl"] = -1000000
    assert strategy_feedback(rows)["closed_trades"] == 15 and strategy_feedback(rows)[
        "expectancy_r"
    ] == pytest.approx(0.2)
    rows[0]["account_modes"] = ["PAPER_SANDBOX"]
    rows[1]["account_modes"] = ["LIVE"]
    assert strategy_feedback(rows)["closed_trades"] == 14
    rows[0]["account_modes"] = []
    assert (
        "strategy_trade_account_mode_unverified" in strategy_feedback(rows)["reasons"]
    )


def test_pending_plan_keeps_top_ranked_and_counts_open_positions():
    plan = pending_entry_plan(
        2,
        [{"id": "a", "score": 50}, {"id": "b", "score": 90}, {"id": "c", "score": 70}],
        4,
    )
    assert plan["keep"] == ["b", "c"] and plan["cancel"] == ["a"]
    assert pending_entry_plan(2, [{"id": "a", "score": 90}], 0)["cancel"] == ["a"]
    assert pending_entry_plan(0, [{"id": "a", "score": 90}], 7, account_known=False)[
        "cancel"
    ] == ["a"]


@pytest.fixture
def pipeline(monkeypatch):
    p = ModuleEPipeline(None)
    calendar = MarketCalendar(pd.bdate_range(end=D, periods=300).date)
    monkeypatch.setattr(
        p,
        "resolve_session",
        lambda d: (datetime(2026, 10, 16, 18, tzinfo=IST), calendar, D, D),
    )
    h = asdict(healthy(closed_trades=0, strategy_expectancy_r=None))
    benchmark = {
        k: h[k]
        for k in (
            "midcap_close",
            "midcap_dma50",
            "midcap_dma200",
            "midcap_dma200_slope_pct",
            "india_vix",
            "india_vix_5d_change_pct",
        )
    }
    breadth = {
        k: h[k]
        for k in (
            "breadth_above_50dma_pct",
            "breadth_above_21ema_pct",
            "advance_decline_10d",
            "new_highs_pct",
            "new_lows_pct",
            "net_highs_trend_pp",
            "valid_rallies_pct",
        )
    }
    monkeypatch.setattr(p, "_benchmarks", lambda *a: benchmark)
    monkeypatch.setattr(
        p,
        "_coverage",
        lambda *a: (
            [{"id": 1}],
            {"master_universe": 1, "unclassified": 0, "exceptions": []},
        ),
    )
    monkeypatch.setattr(
        p,
        "_breadth",
        lambda *a: (breadth, {"measured_liquid_stocks": 1, "exceptions": []}),
    )
    monkeypatch.setattr(p, "_strategy_feedback", lambda *a: strategy_feedback([]))
    monkeypatch.setattr(
        p, "_previous_tracker", lambda *a: (RegimeTracker(), "cold_start")
    )
    return p


def test_pipeline_requires_full_verified_coverage_and_skips_partial_score(
    pipeline, monkeypatch
):
    monkeypatch.setattr(
        pipeline,
        "_coverage",
        lambda *a: (
            [{"id": 1}],
            {"master_universe": 2, "unclassified": 1, "exceptions": []},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_breadth",
        lambda *a: pytest.fail("Cannot score a selected partial universe"),
    )
    inputs, decision = pipeline.evaluate(D, persist=False)
    assert (
        inputs is None
        and not decision.data_ready
        and decision.band == "UNAVAILABLE"
        and decision.max_open_trades == 0
    )
    assert "breadth_liquidity_coverage_incomplete" in decision.reasons


def test_pipeline_valid_input_cold_start_is_warmup_not_fake_market_weakness(pipeline):
    inputs, decision = pipeline.evaluate(D, persist=False)
    assert (
        inputs
        and decision.data_ready
        and decision.max_open_trades == 0
        and "stability_warmup" in decision.reasons
    )


def test_missing_benchmark_still_publishes_unavailable_not_fabricated_price(
    pipeline, monkeypatch
):
    def missing(*a):
        raise ValueError("INDIAVIX_official_sessions_missing_or_invalid")

    monkeypatch.setattr(pipeline, "_benchmarks", missing)
    inputs, decision = pipeline.evaluate(D, persist=False)
    assert inputs is None and not decision.data_ready and decision.max_open_trades == 0


def test_persisted_diagnostics_bound_samples_but_keep_exact_counts():
    q = {"liquidity": {"exceptions": [{"id": i} for i in range(100)]}}
    compact = compact_quality(q)
    assert (
        compact["liquidity"]["exception_count"] == 100
        and len(compact["liquidity"]["exceptions"]) == 25
    )
    assert len(q["liquidity"]["exceptions"]) == 100


def test_module_c_blocks_unavailable_or_legacy_regime_metadata():
    from contextlib import contextmanager
    from packages.pms.module_c_pipeline import ModuleCPipeline

    row = {
        "max_open_trades": 7,
        "hard_override": False,
        "strategy_version": CFG["version"],
        "inputs": {},
    }

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, *a):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    p = ModuleCPipeline(Engine())
    assert p._regime_capacity(D)[0] == 0
    row["inputs"] = {
        "_module_e_version": ENGINE_VERSION,
        "_data_quality": {"data_ready": False},
    }
    assert "market_regime_data_unavailable" in p._regime_capacity(D)[1]
    row["inputs"]["_data_quality"]["data_ready"] = True
    assert p._regime_capacity(D) == (7, ())


def test_benchmark_internal_missing_session_is_rejected_even_with_current_last_date(
    monkeypatch,
):
    p = ModuleEPipeline(None)
    dates = list(pd.bdate_range(end=D, periods=210).date)
    calendar = MarketCalendar(dates)

    def rows(code, expected, start):
        values = [
            dict(trade_date=d, close=100 + i) for i, d in enumerate(dates) if d >= start
        ]
        return values[:10] + values[11:] if code == p.MIDSMALL_CODE else values

    monkeypatch.setattr(p, "_benchmark_rows", rows)
    with pytest.raises(ValueError, match="official_sessions_missing"):
        p._benchmarks(D, calendar)


def test_index_slope_and_vix_use_five_session_change(monkeypatch):
    p = ModuleEPipeline(None)
    dates = list(pd.bdate_range(end=D, periods=210).date)
    monkeypatch.setattr(
        p,
        "_benchmark_rows",
        lambda code, expected, start: [
            dict(trade_date=d, close=100 + i) for i, d in enumerate(dates) if d >= start
        ],
    )
    result = p._benchmarks(D, MarketCalendar(dates))
    assert result["midcap_dma200"] == pytest.approx(sum(range(110, 310)) / 200)
    assert result["midcap_dma200_slope_pct"] > 0
    assert result["india_vix_5d_change_pct"] == pytest.approx((309 / 304 - 1) * 100)


def test_previous_tracker_restores_only_consecutive_matching_checkpoint(monkeypatch):
    from contextlib import contextmanager

    dates = list(pd.bdate_range(end=D, periods=10).date)
    previous = dates[-2]
    tracker = RegimeTracker()
    tracker.update(healthy(), CFG, session=previous)
    row = {
        "trade_date": previous,
        "strategy_version": CFG["version"],
        "inputs": {
            "_module_e_version": ENGINE_VERSION,
            "_tracker_after": tracker.checkpoint(),
        },
    }

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, *a):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    p = ModuleEPipeline(Engine())
    restored, source = p._previous_tracker(D, MarketCalendar(dates))
    assert (
        restored.checkpoint() == tracker.checkpoint()
        and source == "previous_consecutive_session"
    )
    row["trade_date"] = dates[-3]
    restored, source = p._previous_tracker(D, MarketCalendar(dates))
    assert restored.current is None and source == "cold_start_or_data_gap"


def test_weekend_resolution_does_not_create_extra_market_close(monkeypatch):
    dates = list(pd.bdate_range(end=D, periods=210).date)
    monkeypatch.setattr(
        MarketCalendar, "from_database", lambda *a: MarketCalendar(dates, {2026: set()})
    )
    assert ModuleEPipeline(None).resolve_session(date(2026, 10, 18))[2] == D


def test_api_resolves_session_and_forces_unavailable_capacity_to_zero(monkeypatch):
    from contextlib import contextmanager
    import apps.api.main as api

    row = {
        "trade_date": D,
        "band": "STRONG",
        "max_open_trades": 7,
        "strategy_version": CFG["version"],
        "inputs": {
            "_module_e_version": ENGINE_VERSION,
            "_data_quality": {"data_ready": False},
            "_reason": "coverage incomplete",
        },
    }

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, *a):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    monkeypatch.setattr(api, "db_engine", lambda: Engine())
    result = api.module_e_from_database(date(2026, 10, 18))
    assert (
        result["trade_date"] == D
        and result["band"] == "UNAVAILABLE"
        and result["max_open_trades"] == 0
    )


def test_all_advances_and_flat_universes_have_finite_ratios():
    dates = list(pd.bdate_range(end=D, periods=300).date)
    for rising in (True, False):
        rows = []
        for i, d in enumerate(dates):
            c = 100 + i * 0.1 if rising else 100
            rows.append(
                dict(
                    instrument_id=1,
                    trade_date=d,
                    open=c,
                    high=c + 2,
                    low=c - 2,
                    close=c,
                    volume=1000000,
                    turnover=None,
                    source="nse",
                )
            )
        values, q = verified_breadth(
            [{"id": 1, "instrument_id": "NSE:A", "listed_from": None}],
            rows,
            [],
            MarketCalendar(dates),
            D,
            CFG,
        )
        assert values["advance_decline_10d"] == (2 if rising else 1)
        assert q["zero_declines_saturated"] == rising


def test_pending_helper_never_exceeds_seven_even_with_bad_caller_capacity():
    rows = [{"id": str(i), "score": i} for i in range(10)]
    assert len(pending_entry_plan(0, rows, 99)["keep"]) == 7


def test_pending_controls_include_current_fills_after_snapshot():
    p = ModuleEPipeline(None)
    statements = []

    class Result:
        def __init__(self, rows=(), scalar=None):
            self.rows = rows
            self.value = scalar

        def mappings(self):
            return self

        def scalars(self):
            return self

        def all(self):
            return self.rows

        def scalar_one_or_none(self):
            return self.value

    class Conn:
        def execute(self, sql, params=None):
            statements.append(str(sql))
            if "for update" in str(sql):
                return Result([{"id": "a", "score": 50}, {"id": "b", "score": 90}])
            if "select positions" in str(sql):
                return Result(scalar=[])
            if "select distinct t.instrument_id" in str(sql):
                return Result([1, 2])
            return Result()

    plan = p._pending_controls(Conn(), D, 3)
    assert (
        plan["open_positions"] == 2
        and plan["keep"] == ["b"]
        and plan["cancel"] == ["a"]
    )


def test_historical_persistence_cannot_leave_future_regimes_stale():
    from contextlib import contextmanager

    statements = []

    class Engine:
        @contextmanager
        def begin(self):
            yield self

        def execute(self, sql, params=None):
            statements.append(str(sql))
            return SimpleNamespace(scalar_one=lambda: 1)

    p = ModuleEPipeline(Engine())
    with pytest.raises(ValueError, match="chronological replay"):
        p._persist(
            D,
            decide_regime(90, True, 70, CFG),
            {},
            datetime(2026, 10, 16, 18, tzinfo=IST),
        )
    assert not any(s.lstrip().startswith("insert") for s in statements)
