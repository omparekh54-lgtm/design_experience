from copy import deepcopy
from datetime import date, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace
import math
import pytest

from packages.pms.formal_paper import (
    Budget,
    simulate_session,
    FormalPaperExecution,
    VERSION,
    fingerprint,
)
from packages.pms.trade_engine import (
    TradePosition,
    TradeState,
    activate_entry,
    reentry_allowed,
    create_reentry,
    process_protective_stop,
)
from packages.pms.config import load_strategy_config
from packages.pms.module_a_context import IST
from packages.pms.ledger import ledger_positions
from packages.pms.market_calendar import MarketCalendar

CFG = load_strategy_config()
D = date(2026, 10, 19)
IND = dict(ema9=102, previous_ema9=101, previous_ema21=100)


def metadata(**kw):
    p = TradePosition(
        "NSE:TEST",
        date(2026, 10, 16),
        100,
        96,
        1000000,
        entry_sessions=(D, D + timedelta(days=1), D + timedelta(days=2)),
    )
    activate_entry(p)
    result = dict(
        implementation=VERSION,
        checkpoint=p.checkpoint(),
        sector="IT",
        admitted_shares=1000,
        admitted_on="2026-10-16",
        config_hash=fingerprint(CFG),
    )
    result.update(kw)
    return result


def bar(open=100, high=110, low=99, close=108):
    return dict(open=open, high=high, low=low, close=close)


def simulate(meta=None, **kw):
    args = dict(
        metadata=metadata() if meta is None else meta,
        day=D,
        bar=bar(),
        indicators=IND,
        cfg=CFG,
        budget=Budget(1000000, 1000000, 0, 0),
        entry_ready=True,
        fundamental_eligible=True,
        capacity=7,
        adtv=10000000,
    )
    args.update(kw)
    return simulate_session(**args)


def filled():
    meta, fills, issues = simulate()
    assert fills[0]["side"] == "BUY"
    return meta


def test_gap_fill_resizes_at_actual_price_and_preserves_original_limit():
    meta, fills, _ = simulate(bar=bar(open=110, high=115, low=109, close=114))
    p = TradePosition.restore(meta["checkpoint"])
    assert p.entry == 110 and p.shares <= 100000 / 110
    assert p.shares * (p.entry - p.stop) <= 5000 + 1e-8
    assert p.shares <= 1000 and fills[0]["quantity"] == p.shares


def test_same_day_double_touch_is_conservative_and_cash_pnl_conserve():
    meta, fills, _ = simulate(bar=bar(low=94))
    assert [f["side"] for f in fills] == ["BUY", "SELL"]
    assert TradePosition.restore(meta["checkpoint"]).state == TradeState.CLOSED
    cash = sum(
        (
            -f["price"] * f["quantity"]
            if f["side"] == "BUY"
            else f["price"] * f["quantity"]
        )
        - f["fees"]
        for f in fills
    )
    assert cash == pytest.approx(fills[1]["realized"])


@pytest.mark.parametrize(
    "change",
    [
        dict(entry_ready=False),
        dict(fundamental_eligible=False),
        dict(capacity=0),
        dict(indicators=None),
    ],
)
def test_entry_gates_cancel_without_fill(change):
    meta, fills, _ = simulate(**change)
    assert not fills and meta["checkpoint"]["state"] == "CANCELLED"


def test_replay_is_identical_and_conflicting_bar_rejected():
    meta = filled()
    replay, fills, issues = simulate(meta)
    assert replay == meta and not fills and not issues
    with pytest.raises(ValueError, match="conflicting"):
        simulate(meta, bar=bar(close=109))


def test_completed_position_exits_ignore_new_entry_gates_and_missing_emas():
    meta = filled()
    next_day = D + timedelta(days=1)
    updated, fills, issues = simulate(
        meta,
        day=next_day,
        bar=bar(open=90, high=95, low=89, close=92),
        indicators=None,
        entry_ready=False,
        fundamental_eligible=False,
        capacity=0,
    )
    assert fills[0]["side"] == "SELL" and fills[0]["price"] == 90
    assert updated["checkpoint"]["state"] == "CLOSED"


def test_missing_ema_retains_protection_without_inventing_an_exit():
    updated, fills, issues = simulate(
        filled(),
        day=D + timedelta(days=1),
        indicators=None,
        entry_ready=False,
        fundamental_eligible=False,
    )
    assert not fills and issues == ["ema_history_unavailable_protection_only"]
    assert updated["checkpoint"]["remaining_shares"] > 0


def test_next_open_partial_then_stop_uses_exact_quantities_and_net_fees():
    meta = filled()
    pos = TradePosition.restore(meta["checkpoint"])
    qty = pos.remaining_shares
    pos.state = TradeState.PENDING_LEG1_PARTIAL_EXIT
    pos.pending_exit_signal_date = D
    meta["checkpoint"] = pos.checkpoint()
    updated, fills, _ = simulate(
        meta,
        day=D + timedelta(days=1),
        bar=bar(open=105, high=110, low=99, close=108),
        entry_ready=False,
    )
    assert [f["side"] for f in fills] == ["SELL", "SELL"]
    assert sum(f["quantity"] for f in fills) == qty
    assert updated["checkpoint"]["remaining_shares"] == 0


def test_leg2_ema_exit_creates_only_one_reentry_and_stop_does_not():
    meta = filled()
    pos = TradePosition.restore(meta["checkpoint"])
    pos.state = TradeState.PENDING_LEG2_EXIT
    pos.pending_exit_signal_date = D
    pos.reentry_signal_date = D
    pos.reentry_signal_high = 110
    pos.reentry_signal_low = 100
    meta["checkpoint"] = pos.checkpoint()
    updated, fills, _ = simulate(
        meta, day=D + timedelta(days=1), bar=bar(open=104, high=110, low=103, close=108)
    )
    closed = TradePosition.restore(updated["checkpoint"])
    assert reentry_allowed(closed)
    successor = create_reentry(
        closed, 1000000, entry_sessions=tuple(D + timedelta(days=i) for i in (1, 2, 3))
    )
    assert successor.signal_date == D and not reentry_allowed(closed)
    with pytest.raises(ValueError):
        create_reentry(closed, 1000000)
    stopped, _, _ = simulate(
        meta, day=D + timedelta(days=1), bar=bar(open=90, high=95, low=89, close=92)
    )
    assert not reentry_allowed(TradePosition.restore(stopped["checkpoint"]))


@pytest.mark.parametrize(
    "budget,capacity,expected",
    [
        (Budget(1000000, 1000000, 35000, 0), 7, 0),
        (Budget(1000000, 1000000, 0, 7), 7, 0),
        (Budget(1000000, 1000000, 0, 1, {"IT": 2}), 7, 0),
        (Budget(1000000, 1000000, 0, 0, {}, {"NSE:TEST"}), 7, 0),
        (Budget(1000000, 1000000, 0, 0), 2, 1000),
    ],
)
def test_cash_heat_sector_slot_and_duplicate_limits(budget, capacity, expected):
    assert (
        budget.limit(
            CFG,
            instrument="NSE:TEST",
            sector="IT",
            entry=100,
            stop=96,
            adtv=10000000,
            capacity=capacity,
            ceiling=1000,
        )
        == expected
    )


def test_fees_cannot_overdraw_cash_and_liquidity_is_in_rupees():
    meta, fills, _ = simulate(budget=Budget(1000000, 100, 0, 0))
    assert not fills
    _, fills, _ = simulate(adtv=100000)
    assert fills[0]["quantity"] * fills[0]["price"] <= 2000


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_budget_never_creates_shares(value):
    with pytest.raises(ValueError):
        Budget(1000000, value, 0, 0).limit(
            CFG,
            instrument="NSE:X",
            sector="IT",
            entry=100,
            stop=96,
            adtv=10000000,
            capacity=7,
            ceiling=1000,
        )


def test_ledger_basis_resets_between_trade_cycles_and_allocates_entry_fees():
    rows = [
        dict(instrument_id=1, quantity=10, price=100, fees=10),
        dict(instrument_id=1, quantity=-10, price=120, fees=10),
        dict(instrument_id=1, quantity=5, price=200, fees=5),
    ]
    assert ledger_positions(rows) == {1: dict(quantity=5, average_price=201)}
    assert (
        ledger_positions(
            rows + [dict(instrument_id=1, quantity=-2, price=210, fees=1)]
        )[1]["average_price"]
        == 201
    )
    with pytest.raises(ValueError):
        ledger_positions([dict(instrument_id=1, quantity=-1, price=100)])


def test_signal_day_cannot_fill_and_missing_stock_bars_do_not_extend_entry_window():
    meta, fills, _ = simulate(day=date(2026, 10, 16))
    assert not fills and meta["checkpoint"]["state"] == "PENDING_ENTRY"
    meta, fills, _ = simulate(day=D + timedelta(days=3))
    assert not fills and meta["checkpoint"]["state"] == "CANCELLED"


def test_future_or_historical_formal_fill_dates_are_rejected():
    service = FormalPaperExecution(
        None, clock=lambda: datetime(2026, 10, 19, 19, tzinfo=IST)
    )
    with pytest.raises(ValueError):
        service.session(date(2026, 10, 16))
    with pytest.raises(ValueError):
        service.session(date(2026, 10, 20))


def test_simple_executor_cannot_bypass_formal_lifecycle():
    from packages.pms.paper_execution import PaperExecutionService

    with pytest.raises(PermissionError):
        PaperExecutionService(None)


def test_completed_session_guard_uses_sourced_calendar(monkeypatch):
    service = FormalPaperExecution(
        None, clock=lambda: datetime(2026, 10, 19, 14, tzinfo=IST)
    )
    monkeypatch.setattr(
        MarketCalendar,
        "from_database",
        lambda *a: MarketCalendar([date(2026, 10, 16)], {2026: set()}),
    )
    assert service.session()[2:] == ("SKIPPED", "market_not_closed")
    service.clock = lambda: datetime(2026, 10, 19, 19, tzinfo=IST)
    assert service.session()[2:] == ("BLOCKED", "official_session_not_observed")


def test_sector_caps_normalize_case_and_whitespace():
    budget = Budget(1000000, 1000000, 0, 2, {" IT ": 1, "it": 1})
    assert (
        budget.limit(
            CFG,
            instrument="NSE:X",
            sector="It",
            entry=100,
            stop=96,
            adtv=10000000,
            capacity=7,
            ceiling=1000,
        )
        == 0
    )


def test_naive_paper_clock_is_rejected():
    service = FormalPaperExecution(None, clock=lambda: datetime(2026, 10, 19, 19))
    with pytest.raises(ValueError, match="timezone aware"):
        service.session()
