from dataclasses import replace, asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import math

import pandas as pd
import pytest

from packages.pms.config import load_strategy_config
from packages.pms.module_c import *
from packages.pms.module_c_inputs import (
    portfolio_state,
    ranking_inputs,
    measure_features,
)
from packages.pms.module_c_pipeline import ModuleCPipeline, _percentile_map
from packages.pms.module_b_pipeline import ModuleBResult
from packages.pms.module_a_context import IST
from packages.pms.market_calendar import MarketCalendar
from packages.data_layer.models import Candle

CFG = load_strategy_config()
D = date(2026, 10, 16)
STAMP = datetime(2026, 10, 18, 18, tzinfo=IST)


def candidate(name="NSE:A", **kw):
    row = Candidate(
        name,
        "IT",
        True,
        True,
        1,
        4,
        100000,
        10000000,
        False,
        1000,
        3,
        RankingInputs(90, 90, 90, 90, 90, 90),
    )
    return replace(row, **kw)


def state(**kw):
    return replace(AllocationState(0, 0, 0, 1000000, {}, nav=1000000), **kw)


def allocation(rows=None, s=None, capacity=7):
    return allocate_candidates(
        [candidate()] if rows is None else rows,
        state() if s is None else s,
        capacity,
        CFG,
    )


def test_score_components_sum_and_exact_minimum():
    result = rank_candidate(RankingInputs(60, 60, 60, 60, 60, 60), CFG)
    assert result.score == 60 and result.qualified
    assert sum(result.components.values()) == 60
    assert not rank_candidate(RankingInputs(59, 59, 59, 59, 59, 59), CFG).qualified


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_ranking_cannot_qualify(value):
    with pytest.raises(ValueError):
        rank_candidate(RankingInputs(value, 100, 100, 100, 100, 100), CFG)
    with pytest.raises(ValueError):
        _percentile_map({1: value})


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"module_a_eligible": False}, "module_a_ineligible"),
        ({"blackout_clear": False}, "earnings_blackout"),
        ({"signal_age_trading_days": 0}, "signal_expired"),
        ({"signal_age_trading_days": 4}, "signal_expired"),
        ({"adr20_pct": 2.99}, "adr"),
        ({"shares": 0}, "position_size_zero"),
        ({"shares": 1.5}, "position_size_zero"),
        ({"adtv": 4999999}, "position_gt_2pct_adtv"),
        ({"sector": ""}, "sector_unavailable"),
        ({"position_value": float("nan")}, "invalid_candidate_data"),
        ({"already_held_or_pending": True}, "already_held_or_pending"),
        ({"stop_risk_pct": 5.6}, "stop_risk_cap"),
    ],
)
def test_stage_one_rejections(change, reason):
    result = allocation([candidate(**change)])
    assert not result.selected and reason in result.rejected["NSE:A"]


@pytest.mark.parametrize("age", [1, 2, 3])
def test_three_valid_entry_days_and_inclusive_adr_liquidity(age):
    assert allocation(
        [candidate(signal_age_trading_days=age, adr20_pct=3, adtv=5000000)]
    ).selected


def test_heat_uses_fixed_nav_and_actual_uncapped_risk():
    rows = [candidate("A", sector="IT"), candidate("B", sector="Industrials")]
    result = allocation(rows, state(cash=300000, nav=1000000, portfolio_heat_pct=2.9))
    assert len(result.selected) == 2 and result.projected_heat_pct == 3.5
    assert result.remaining_cash == 100000
    assert not allocation([candidate()], state(portfolio_heat_pct=3.21)).selected


def test_heat_not_clipped_to_trade_budget_and_position_cap_enforced():
    result = allocation([candidate(position_value=110000, stop_risk_pct=5)])
    assert {"position_cap", "trade_risk_cap"}.issubset(result.rejected["NSE:A"])


def test_geometry_mismatch_cannot_understate_risk():
    assert (
        "risk_geometry_mismatch"
        in allocation([candidate(rupee_risk=1)]).rejected["NSE:A"]
    )


def test_pending_counts_canonical_sector_and_capacity_never_above_seven():
    result = allocation(
        [candidate()],
        state(open_positions=1, pending_entries=1, sector_counts={" it ": 1, "IT": 1}),
    )
    assert "sector_limit" in result.rejected["NSE:A"]
    rows = [
        candidate(str(i), sector=str(i), position_value=90000, shares=900)
        for i in range(10)
    ]
    assert len(allocation(rows, capacity=99).selected) == 7
    assert "capacity" in allocation(capacity=0).rejected["NSE:A"]


def test_cash_reserves_estimated_costs():
    c = candidate(estimated_costs=500)
    assert not allocation([c], state(cash=100000)).selected
    assert allocation([c], state(cash=100500)).remaining_cash == 0


def test_duplicate_input_cannot_allocate_same_instrument_twice():
    result = allocation([candidate(), candidate()])
    assert not result.selected and result.rejected["NSE:A"] == ("duplicate_candidate",)


def test_stable_ties_order_score_then_risk_then_adtv_then_symbol():
    rows = [
        candidate("C"),
        candidate("B", stop_risk_pct=2),
        candidate("A", stop_risk_pct=2),
    ]
    assert [c.instrument_id for c in allocation(rows, capacity=1).selected] == ["A"]


@pytest.mark.parametrize(
    "s",
    [
        state(nav=0),
        state(cash=-1),
        state(portfolio_heat_pct=float("nan")),
        state(open_positions=1, sector_counts={}),
    ],
)
def test_invalid_account_state_cannot_allocate(s):
    assert not allocation(s=s).selected


def snapshot(**kw):
    return dict(
        snapshot_date=D,
        created_at=STAMP - timedelta(minutes=1),
        cash=900000,
        nav=1000000,
        positions=[{"instrument_id": 1, "quantity": 1000}],
        **kw,
    )


def trade(**kw):
    row = dict(
        id="trade1",
        instrument_id=1,
        state="FILLED",
        entry=100,
        stop=95,
        shares=1000,
        remaining_shares=1000,
        updated_at=STAMP - timedelta(minutes=1),
        account_modes=["PAPER"],
    )
    return {**row, **kw}


def account(snap=None, trades=None, orders=()):
    return portfolio_state(
        snapshot() if snap is None else snap,
        [trade()] if trades is None else trades,
        list(orders),
        {1: "IT", 2: "Industrials"},
        D,
        STAMP,
        CFG,
    )


def test_reconciled_positions_and_open_risk():
    s, held = account()
    assert (
        not s.blockers
        and held == {1}
        and s.open_positions == 1
        and s.portfolio_heat_pct == 0.5
    )


def test_missing_snapshot_is_not_default_paper_capital():
    s, _ = portfolio_state(None, [], [], {}, D, STAMP, CFG)
    assert s.nav == 0 and s.cash == 0 and "portfolio_snapshot_unavailable" in s.blockers


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"snapshot_date": D - timedelta(days=1)}, "portfolio_snapshot_stale"),
        (
            {"created_at": STAMP + timedelta(seconds=1)},
            "portfolio_snapshot_not_knowable",
        ),
        ({"positions": []}, "portfolio_positions_unreconciled"),
    ],
)
def test_account_snapshot_failures(changes, reason):
    assert reason in account({**snapshot(), **changes})[0].blockers


def test_live_or_sandbox_trades_do_not_count_as_paper():
    snap = {**snapshot(), "positions": []}
    s, held = account(snap, [trade(account_modes=["PAPER_SANDBOX"])])
    assert not s.blockers and not held and s.open_positions == 0


def test_unknown_trade_account_or_stop_blocks_selection():
    assert (
        "trade_account_mode_unverified"
        in account(trades=[trade(account_modes=[])])[0].blockers
    )
    assert (
        "portfolio_stop_or_size_unavailable"
        in account(trades=[trade(stop=None)])[0].blockers
    )
    assert (
        "historical_account_state_unavailable"
        in account(trades=[trade(updated_at=STAMP + timedelta(days=1))])[0].blockers
    )


def test_pending_orders_reserve_cash_heat_and_sector_slot_once():
    t = trade(state="PENDING_ENTRY", remaining_shares=0)
    order = dict(
        instrument_id=1,
        side="BUY",
        quantity=1000,
        filled_quantity=0,
        strategy_trade_id="trade1",
        trigger_price=100,
        limit_price=None,
        updated_at=STAMP - timedelta(minutes=1),
    )
    s, held = account({**snapshot(), "positions": []}, [t], [order])
    assert (
        not s.blockers
        and s.pending_entries == 1
        and s.open_positions == 0
        and held == {1}
    )
    assert (
        s.cash < 800000 and s.portfolio_heat_pct == 0.5 and s.sector_counts == {"IT": 1}
    )


def test_unlinked_manual_pending_buy_blocks_unknown_risk():
    order = dict(
        instrument_id=2,
        side="BUY",
        quantity=100,
        filled_quantity=0,
        strategy_trade_id=None,
        trigger_price=100,
        updated_at=STAMP,
    )
    s, _ = account(orders=[order])
    assert "pending_order_stop_or_trade_unverified" in s.blockers


@pytest.fixture
def pipeline(monkeypatch):
    p = ModuleCPipeline(None)
    dates = pd.bdate_range(end=D, periods=270)
    calendar = MarketCalendar(dates.date, {2026: {date(2026, 10, 20)}})
    monkeypatch.setattr(p.b, "_calendar", lambda _: calendar)
    monkeypatch.setattr(p, "_benchmark_returns", lambda *a: {63: 0, 126: 0})
    monkeypatch.setattr(p, "_portfolio_state", lambda *a: (state(), set()))
    monkeypatch.setattr(p, "_regime_capacity", lambda *a: (7, ()))

    def arow(i):
        return {
            "instrument_id": f"NSE:S{i}",
            "snapshot": {"sector": "IT", "reference_context": {"id": i}},
            "decision": {
                "eligible": True,
                "score": 90,
                "triggers": ["record_sales"],
                "failed_rules": [],
            },
        }

    monkeypatch.setattr(
        p.a, "scan", lambda *a, **kw: {"eligible": 1, "results": [arow(1)]}
    )
    candles = []
    for i, d in enumerate(dates):
        close = Decimal(str(100 + i * 0.05))
        candles.append(
            Candle(
                "NSE:S1",
                d.to_pydatetime(),
                close,
                close + 5,
                close - 5,
                close,
                1000000,
                "test",
            )
        )
    monkeypatch.setattr(p.b, "_candles", lambda *a: candles)
    signal = {
        "setup": "SETUP_1",
        "status": "SIGNAL_CREATED",
        "signal_date": D.isoformat(),
        "signal_high": 120,
        "signal_low": 110,
        "velocity": 2,
        "volume_ratio": 3,
    }

    def b(instrument, d, *, require_module_a):
        assert require_module_a
        return ModuleBResult(instrument, d, True, (), dict(signal))

    monkeypatch.setattr(p.b, "evaluate", b)
    monkeypatch.setattr(
        p.b.reference, "earnings_within_trading_window", lambda *a, **kw: False
    )
    p.test_arow = arow
    p.test_signal = signal
    return p


def test_complete_signal_order_sheet_and_calendar_days_left(pipeline):
    result = pipeline.generate(D, persist=False)
    assert result.selected == 1 and result.qualified == 1 and not result.blockers
    row = result.order_sheet[0]
    assert (
        row["days_left"] == 3
        and row["next_entry_session"] == "2026-10-19"
        and row["expires_on"] == "2026-10-22"
    )
    assert row["rank"] == 1 and row["buy_stop"] == 120 and row["rupee_risk"] <= 5000
    assert len(row["components"]) == 6 and row["estimated_entry_costs"] > 0


def test_watchlist_is_ranked_without_creating_orders(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline.b,
        "evaluate",
        lambda i, d, **kw: ModuleBResult(
            i, d, True, (), None, watchlist=pipeline.test_signal
        ),
    )
    result = pipeline.generate(D, persist=False)
    assert (
        result.selected == 0
        and len(result.watchlist) == 1
        and result.watchlist[0]["watchlist_rank"] == 1
    )


def test_pipeline_explicit_rejection_and_a_block_skips_b(pipeline, monkeypatch):
    row = pipeline.test_arow(1)
    row["decision"]["eligible"] = False
    row["decision"]["failed_rules"] = ["missing_balance_sheet"]
    monkeypatch.setattr(
        pipeline.a, "scan", lambda *a, **kw: {"eligible": 0, "results": [row]}
    )
    monkeypatch.setattr(
        pipeline.b, "evaluate", lambda *a, **kw: pytest.fail("A gate failed")
    )
    result = pipeline.generate(D, persist=False)
    assert result.rejected == 1 and result.results[0]["module_a_reasons"] == [
        "missing_balance_sheet"
    ]


def test_missing_benchmark_never_substitutes_nifty50(pipeline, monkeypatch):
    def missing(*a):
        raise ValueError("nifty500_history_unavailable")

    monkeypatch.setattr(pipeline, "_benchmark_returns", missing)
    result = pipeline.generate(D, persist=False)
    assert not result.order_sheet and "nifty500_history_unavailable" in result.blockers


def test_missing_regime_and_portfolio_block_a_high_score(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline, "_regime_capacity", lambda *a: (0, ("market_regime_unavailable",))
    )
    monkeypatch.setattr(
        pipeline,
        "_portfolio_state",
        lambda *a: (state(blockers=("unreconciled",)), set()),
    )
    result = pipeline.generate(D, persist=False)
    assert not result.order_sheet and "unreconciled" in result.blockers
    assert "market_regime_unavailable" in result.results[0]["rejected_reasons"]


def test_research_and_setup2_signals_cannot_be_selected(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline.b,
        "evaluate",
        lambda i, d, **kw: ModuleBResult(
            i, d, True, (), pipeline.test_signal, research_only=True
        ),
    )
    assert pipeline.generate(D, persist=False).results[0]["rejected_reasons"] == [
        "research_signal_not_production"
    ]
    monkeypatch.setattr(
        pipeline.b,
        "evaluate",
        lambda i, d, **kw: ModuleBResult(
            i, d, True, (), {**pipeline.test_signal, "setup": "SETUP_2"}
        ),
    )
    assert not pipeline.generate(D, persist=False).order_sheet


def test_blackout_checks_planned_order_session_not_previous_close(
    pipeline, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        pipeline.b.reference,
        "earnings_within_trading_window",
        lambda *a, **kw: seen.append(a) or True,
    )
    result = pipeline.generate(D, persist=False)
    assert seen[0][1] == date(2026, 10, 19)
    assert seen[0][2][-1] == date(2026, 10, 23)  # holiday Oct 20 is skipped
    assert "earnings_blackout" in result.results[0]["rejected_reasons"]


def test_measurements_use_three_and_six_month_relative_returns(pipeline):
    from packages.data_layer.features import compute_features

    frame = compute_features(pipeline.b._candles("", D)).data
    stats = measure_features(frame, D, {63: 5, 126: 10})
    expected = (
        float(frame.iloc[-1]["close"]) / float(frame.iloc[-64]["close"]) / 1.05 - 1
    ) * 100
    assert stats["relative_63"] == pytest.approx(expected)
    assert stats["adtv"] == pytest.approx(float(frame.iloc[-20:]["turnover"].mean()))


def test_stage3_and_volume_contribute_to_score():
    stats = dict(
        pullback_depth_atr=0,
        above_dma50=True,
        ema21_rising=True,
        signal_close_location=1,
        distance_52w=0,
    )
    base = ranking_inputs(
        {"velocity": 1, "volume_ratio": 1.5}, stats, 70, False, 50, 50, CFG
    )
    boosted = ranking_inputs(
        {"velocity": 2, "volume_ratio": 3}, stats, 70, True, 50, 50, CFG
    )
    assert (
        boosted.rally_quality > base.rally_quality
        and boosted.fundamental_strength == base.fundamental_strength + 10
    )


def test_unreconciled_nav_and_duplicate_pending_broker_orders_block():
    assert (
        "portfolio_nav_unreconciled"
        in account({**snapshot(), "market_value": 1})[0].blockers
    )
    t = trade(state="PENDING_ENTRY", remaining_shares=0)
    order = dict(
        instrument_id=1,
        side="BUY",
        quantity=1000,
        filled_quantity=0,
        strategy_trade_id="trade1",
        trigger_price=100,
        updated_at=STAMP,
    )
    s, _ = account({**snapshot(), "positions": []}, [t], [order, order])
    assert "multiple_pending_orders_for_trade" in s.blockers


def test_after_close_pipeline_rejects_intraday_generation(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline, "_as_of_end", lambda d: datetime(2026, 10, 16, 14, tzinfo=IST)
    )
    with pytest.raises(ValueError, match="after market close"):
        pipeline.generate(D, persist=False)


def test_persist_sql_binds_json_values_without_phantom_boolean_parameters(pipeline):
    from contextlib import contextmanager

    seen = []

    class Engine:
        @contextmanager
        def begin(self):
            yield self

        def execute(self, statement, params):
            assert set(statement._bindparams).issubset(params)
            assert "false" not in statement._bindparams
            seen.append(str(statement))

    report = pipeline.generate(D, persist=False)
    pipeline.engine = Engine()
    pipeline._persist(report, STAMP)
    assert seen and not any(s.lstrip().lower().startswith("delete") for s in seen)


def test_api_returns_order_sheet_watchlist_and_numeric_components(monkeypatch):
    from contextlib import contextmanager
    import apps.api.main as api

    rows = [
        dict(
            details={"selected": True, "rank": 1, "kind": "SIGNAL"},
            score=80,
            selected=True,
            components={"rally_quality": 20},
        ),
        dict(
            details={"selected": False, "watchlist_rank": 1, "kind": "WATCHLIST"},
            score=70,
            selected=False,
            components={"rally_quality": 15},
        ),
    ]

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, statement, params):
            assert params["version"].endswith("MODULE_C_v1.1:PAPER")
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    monkeypatch.setattr(api, "db_engine", lambda: Engine())
    result = api.module_c_rankings(D, False, 100)
    assert len(result["order_sheet"]) == 1 and len(result["watchlist"]) == 1
    assert result["rankings"][0]["components"]["rally_quality"] == 20
    assert result["proposal_only"] and result["broker_orders_submitted"] == 0


def test_nifty500_fetcher_requests_correct_official_index_name():
    from scripts.ingest_module_e_benchmarks import fetch_window

    seen = []

    class NSE:
        def fetch_historical_index_data(self, name, **kw):
            seen.append(name)
            return [{"EOD_TIMESTAMP": "16-Oct-2026", "EOD_CLOSE_INDEX_VAL": "23000"}]

    rows, _ = fetch_window(NSE(), "NIFTY500", D, D)
    assert seen == ["NIFTY 500"] and rows[0]["benchmark_code"] == "NIFTY500"


def test_pending_quote_cannot_understate_reserved_stop_risk():
    t = trade(state="PENDING_ENTRY", remaining_shares=0)
    order = dict(
        instrument_id=1,
        side="BUY",
        quantity=1000,
        filled_quantity=0,
        strategy_trade_id="trade1",
        trigger_price=110,
        updated_at=STAMP,
    )
    s, _ = account({**snapshot(), "positions": []}, [t], [order])
    assert "pending_order_trade_mismatch" in s.blockers


def test_orchestrator_persists_upstream_a_b_and_c(pipeline, monkeypatch):
    calls = []
    original = pipeline.a.scan

    def scan(stamp, *, persist):
        calls.append(("A", persist))
        return {**original(stamp, persist=persist), "configuration_ready": True}

    monkeypatch.setattr(pipeline.a, "scan", scan)
    monkeypatch.setattr(
        pipeline.b,
        "persist_results",
        lambda rows, stamp: calls.append(("B", len(rows))),
    )
    monkeypatch.setattr(
        pipeline, "_persist", lambda report, stamp: calls.append(("C", report.selected))
    )
    report = pipeline.generate(D, persist=True, persist_upstream=True)
    assert calls == [("A", True), ("B", 1), ("C", 1)]
    assert report.upstream["a_persisted"] and report.upstream["b_evaluated"] == 1


def test_orchestrator_configuration_blocker_prevents_selected_proposals(
    pipeline, monkeypatch
):
    monkeypatch.setattr(pipeline.b, "persist_results", lambda *args: None)
    monkeypatch.setattr(pipeline, "_persist", lambda *args: None)
    report = pipeline.generate(D, persist=True, persist_upstream=True)
    assert report.selected == 0
    assert "module_a_configuration_not_ready" in report.blockers


def test_read_only_c_cannot_request_upstream_writes(pipeline):
    with pytest.raises(ValueError, match="Upstream persistence"):
        pipeline.generate(D, persist=False, persist_upstream=True)


def test_orchestrator_empty_a_universe_blocks_daily_allocation(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline.a,
        "scan",
        lambda *a, **kw: dict(eligible=0, results=[], configuration_ready=True),
    )
    monkeypatch.setattr(pipeline.b, "persist_results", lambda *a: None)
    monkeypatch.setattr(pipeline, "_persist", lambda *a: None)
    report = pipeline.generate(D, persist=True, persist_upstream=True)
    assert report.universe_size == 0 and report.selected == 0
    assert "module_a_universe_unavailable" in report.blockers
