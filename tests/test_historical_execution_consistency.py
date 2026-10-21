from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from packages.pms.backtest import EventDrivenBacktester, MarketBar, Signal
from packages.pms.execution_timing import intraday_information_changed, intraday_trigger
from packages.pms.matrix_replay import MatrixReplay, ResearchDataGap
from packages.pms.trade_engine import TradePosition, TradeState, activate_entry

D = date(2020, 2, 3)
DAYS = [D + timedelta(days=i) for i in range(5)]


def bar(day, symbol="NSE:A", **kw):
    return MarketBar(symbol, day, **dict(
        dict(open=90, high=92, low=89, close=91, ema9=90,
             previous_ema9=90, previous_ema21=89), **kw))


@pytest.mark.parametrize("close_cut", [False, True])
def test_capacity_cancels_lowest_score_even_when_it_is_oldest(close_cut):
    b = EventDrivenBacktester(1e6)
    def signals(day):
        if day == D:
            return [Signal("NSE:A", D, 100, 95, 60), Signal("NSE:B", D, 100, 95, 90)]
        return []
    b.run([bar(d, s) for d in DAYS[:2] for s in ("NSE:A", "NSE:B")], signals,
          capacity_provider=lambda d: 7 if close_cut or d == D else 1,
          close_capacity_provider=lambda d: 1 if close_cut and d > D else 7)
    assert b.positions["NSE:A"].exit_reason == "regime_capacity"
    assert b.positions["NSE:B"].state == TradeState.PENDING_ENTRY
    assert any(e.payload.get("reason") == "regime_capacity" for e in b.events)


@pytest.mark.parametrize("ranks,loser", [
    ({"NSE:A": (80, 6, 1e8), "NSE:B": (80, 5, 1e7)}, "NSE:A"),
    ({"NSE:A": (80, 5, 1e7), "NSE:B": (80, 5, 1e8)}, "NSE:A"),
    ({"NSE:A": (90, 5, 1e7), "NSE:B": (70, 5, 1e8)}, "NSE:B"),
])
def test_pending_ranking_uses_latest_score_then_risk_then_liquidity(ranks, loser):
    b = EventDrivenBacktester(1e6)
    b.run([bar(d, s) for d in DAYS[:2] for s in ranks],
          lambda d: [Signal(s, D, 100, 95) for s in ranks] if d == D else [],
          capacity_provider=lambda d: 7 if d == D else 1,
          close_capacity_provider=lambda d: 7,
          pending_rank_provider=lambda p, d: ranks[p.instrument_id])
    assert b.positions[loser].exit_reason == "regime_capacity"


def closed_parent():
    p = TradePosition("NSE:A", D - timedelta(days=10), 100, 95, 1e6)
    p.state = TradeState.CLOSED
    p.exit_reason = "leg2_ema21"
    p.reentry_signal_date = D
    p.reentry_signal_high, p.reentry_signal_low = 100, 95
    return p


def test_allocated_reentry_waits_for_next_session_and_keeps_exit_candle_expiry():
    b = EventDrivenBacktester(1e6)
    parent = closed_parent()
    b.positions[parent.instrument_id] = parent
    result = b.run([bar(d, open=101, high=103, low=100, close=102) for d in DAYS[:3]],
                  lambda d: [Signal("NSE:A", D, 100, 95, reentry=True)] if d == DAYS[1] else [],
                  trading_sessions=DAYS, allow_live_signals=True, auto_reentry=False)
    assert parent.reentry_created
    assert result.fills[0].trade_date == DAYS[2]
    assert b.positions["NSE:A"].entry_sessions == tuple(DAYS[1:4])


def test_unallocated_closed_parent_never_reenters():
    b = EventDrivenBacktester(1e6)
    b.positions["NSE:A"] = closed_parent()
    result = b.run([bar(d, open=101, high=103, low=100, close=102) for d in DAYS],
                  lambda d: [], auto_reentry=False)
    assert not result.fills
    assert not b.positions["NSE:A"].reentry_created


def test_entry_timing_failure_precedes_position_and_cash_mutation():
    b = EventDrivenBacktester(1e6)
    def guard(p, bar):
        raise ResearchDataGap("intraday_filing_trigger_order_unknown")
    with pytest.raises(ResearchDataGap, match="intraday_filing"):
        b.run([bar(D), bar(DAYS[1], high=101)],
              lambda d: [Signal("NSE:A", D, 100, 95)] if d == D else [],
              entry_timing_guard=guard)
    assert not b.fills and b.cash == 1e6
    assert b.positions["NSE:A"].state == TradeState.PENDING_ENTRY


@pytest.mark.parametrize("opening,high,queries", [(100, 105, 0), (101, 105, 0), (90, 99, 0), (90, 105, 1)])
def test_matrix_blocks_only_ambiguous_intraday_entries(monkeypatch, opening, high, queries):
    replay = object.__new__(MatrixReplay)
    replay.case = SimpleNamespace(variant=SimpleNamespace(use_module_a=True))
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    replay.engine = SimpleNamespace(connect=lambda: Conn())
    replay.b = SimpleNamespace(prices=SimpleNamespace(_db_instrument_id=lambda *a: 7))
    calls = []
    def changed(conn, pk, day):
        calls.append((pk, day))
        return True
    monkeypatch.setattr("packages.pms.matrix_replay.intraday_information_changed", changed)
    p = activate_entry(TradePosition("NSE:A", D, 100, 95, 1e6))
    if queries:
        with pytest.raises(ResearchDataGap, match="intraday_filing"):
            replay.entry_timing_guard(p, bar(DAYS[1], open=opening, high=high))
    else:
        replay.entry_timing_guard(p, bar(DAYS[1], open=opening, high=high))
    assert len(calls) == queries


def test_shared_information_query_uses_exchange_session_bounds():
    calls = []
    conn = SimpleNamespace(execute=lambda sql, params: (
        calls.append((str(sql), params)) or SimpleNamespace(scalar_one=lambda: 1)))
    assert intraday_information_changed(conn, 7, D)
    sql, params = calls[0]
    assert all(table in sql for table in ("financial_filings", "shareholding_history", "earnings_calendar"))
    assert params["opened"].hour == 9 and params["opened"].minute == 15
    assert params["closed"].hour == 15 and params["closed"].minute == 30
    assert intraday_trigger(90, 100, 100)


def test_c_entry_requires_actual_previous_close_admission():
    replay = object.__new__(MatrixReplay)
    replay.case = SimpleNamespace(variant=SimpleNamespace(use_module_c=True))
    replay.admissions = {}
    p = activate_entry(TradePosition("NSE:A", D, 100, 95, 1e6))
    assert replay.entry_guard(p, bar(DAYS[1]), 100) == 0
    replay.admissions[("NSE:A", D)] = dict(day=DAYS[1], shares=10)
    assert replay.entry_guard(p, bar(DAYS[1]), 100) == 0

@pytest.mark.parametrize("settled", [False, True])
def test_reentry_competes_in_c_only_after_exit_settles(settled):
    from collections import Counter
    replay = object.__new__(MatrixReplay)
    replay.case = SimpleNamespace(variant=SimpleNamespace(use_module_c=True, reentry=True), seed=None)
    parent = closed_parent()
    if not settled:
        parent.state = TradeState.PENDING_LEG2_EXIT
    replay.broker = SimpleNamespace(positions={"NSE:A": parent})
    replay.metadata, replay.last_analysis, replay.admissions = {}, {}, {}
    replay.rejections, replay.decisions = Counter(), []
    calls = []
    def generate(day, **kwargs):
        calls.append(kwargs["reentry_signals"])
        # C rejects the re-entry in favour of an independently ranked competitor.
        return SimpleNamespace(blockers=(), rejection_counts={}, results=[],
            universe_size=2, qualified=2, selected=1,
            order_sheet=[dict(instrument_id="NSE:B", score=90, shares=25,
                buy_stop=100, stop=95, stats=dict(adtv=1e8),
                signal=dict(signal_date=DAYS[1].isoformat(), signal_high=100, signal_low=95))])
    replay.c = SimpleNamespace(generate=generate)
    selected = list(replay.signals(DAYS[1]))
    assert ("NSE:A" in calls[0]) is settled
    assert [s.instrument_id for s in selected] == ["NSE:B"]
    assert not selected[0].reentry
    assert ("NSE:A", D) not in replay.admissions
    assert replay.admissions[("NSE:B", DAYS[1])]["shares"] == 25


def test_filled_holdings_are_retained_when_capacity_falls_to_zero():
    b = EventDrivenBacktester(1e6)
    b.run([bar(d, open=100, high=102, low=99, close=101) for d in DAYS[:3]],
          lambda d: [Signal("NSE:A", D, 100, 95)] if d == D else [],
          capacity_provider=lambda d: 0 if d == DAYS[2] else 7,
          close_capacity_provider=lambda d: 0 if d == DAYS[2] else 7)
    assert b.positions["NSE:A"].remaining_shares > 0
    assert b.positions["NSE:A"].exit_reason is None
