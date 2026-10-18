from datetime import date

from packages.pms.backtest import EventDrivenBacktester, EventType, MarketBar, Signal
from packages.pms.config import load_strategy_config
from packages.pms.costs import estimate_delivery_costs


def test_delivery_costs_apply_statutory_sides_and_2026_exchange_rate():
    cfg = load_strategy_config()["backtest"]
    buy = estimate_delivery_costs(
        trade_date=date(2026, 3, 2), side="BUY", price=100.0, quantity=100, cfg=cfg
    )
    sell = estimate_delivery_costs(
        trade_date=date(2026, 3, 2), side="SELL", price=100.0, quantity=100, cfg=cfg
    )
    assert buy.stt == 10.0
    assert sell.stt == 10.0
    assert buy.stamp_duty == 1.5
    assert sell.stamp_duty == 0.0
    assert buy.exchange_charge == 0.31
    assert buy.sebi_fee == 0.01
    assert buy.slippage == 10.0
    assert buy.impact == 5.0


def test_event_backtester_executes_next_open_exits_and_leg2_reentry():
    bars = [
        MarketBar("NSE:ABC", date(2026, 10, 1), 98, 101, 97, 100, 99, 98, 97),
        # OHLC ranges must contain both the open and close.
        MarketBar("NSE:ABC", date(2026, 10, 2), 100, 103, 96, 103, 102, 101, 98),
        MarketBar("NSE:ABC", date(2026, 10, 3), 104, 106, 102, 102, 103, 103, 99),
        MarketBar("NSE:ABC", date(2026, 10, 4), 105, 107, 104, 104, 103, 103, 102),
        MarketBar("NSE:ABC", date(2026, 10, 5), 104, 105, 101, 102, 103, 103, 103),
        MarketBar("NSE:ABC", date(2026, 10, 6), 101, 106, 101, 105, 103, 103, 103),
    ]

    def signals(d):
        if d == date(2026, 10, 1):
            return [Signal("NSE:ABC", d, 100.0, 95.0)]
        return []

    result = EventDrivenBacktester(initial_capital=1_000_000).run(bars, signals)
    buys = [f for f in result.fills if f.side == "BUY"]
    sells = [f for f in result.fills if f.side == "SELL"]
    assert len(buys) == 2  # original entry + automatic Leg2 re-entry
    assert sells[0].trade_date == date(2026, 10, 4)  # Leg1 signal was Oct 3 close
    assert sells[-1].trade_date == date(2026, 10, 6)  # Leg2 signal was Oct 5 close
    assert any(e.event_type == EventType.REENTRY_SIGNAL for e in result.events)
    assert result.total_costs > 0
    assert result.closed_trades >= 1


def test_backtester_respects_max_open_trade_capacity():
    cfg = load_strategy_config()
    cfg["risk"]["max_open_trades"] = 1
    bars = [
        MarketBar("NSE:A", date(2026, 1, 1), 100, 101, 99, 100, 99, 98, 97),
        MarketBar("NSE:B", date(2026, 1, 1), 100, 101, 99, 100, 99, 98, 97),
        MarketBar("NSE:A", date(2026, 1, 2), 100, 102, 96, 101, 100, 99, 98),
        MarketBar("NSE:B", date(2026, 1, 2), 100, 102, 96, 101, 100, 99, 98),
    ]

    def signals(d):
        if d == date(2026, 1, 1):
            return [Signal("NSE:A", d, 100, 95), Signal("NSE:B", d, 100, 95)]
        return []

    result = EventDrivenBacktester(initial_capital=1_000_000, cfg=cfg).run(bars, signals)
    assert len([f for f in result.fills if f.side == "BUY"]) == 1
