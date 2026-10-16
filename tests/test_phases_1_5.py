from datetime import date

import pandas as pd

from packages.data_layer.corporate_actions import adjust_ohlcv_for_actions
from packages.data_layer.reference_data import CorporateAction, market_cap_cr, split_adjustment_factor
from packages.data_layer.reference_storage import parse_nse_shareholding_row
from packages.pms.config import load_strategy_config
from packages.pms.trade_engine import (
    TradePosition,
    TradeState,
    activate_entry,
    process_entry_bar,
    process_next_open,
    process_position_bar,
    reentry_allowed,
)


def test_historical_market_cap_is_in_crores():
    assert market_cap_cr(100.0, 100_000_000) == 1000.0


def test_parse_nse_shareholding_and_pledge_fields():
    row = {
        "As on Date": "30-SEP-2026",
        "Broadcast Date/Time": "07-OCT-2026 13:32:34",
        "Promoter & Promoter Group (A)": "63.18",
        "% of Promoter shares (X/A)": "12.5",
        "Total no. of issued shares A+B+C": "10000000",
    }
    parsed = parse_nse_shareholding_row("NSE:ABC", row)
    assert parsed.period_end == date(2026, 9, 30)
    assert parsed.promoter_holding_pct == 63.18
    assert parsed.promoter_pledge_pct == 12.5
    assert parsed.total_shares == 10_000_000


def test_split_and_bonus_factors():
    split = CorporateAction("NSE:ABC", "SPLIT", date(2026, 6, 1), 1, 5)
    bonus = CorporateAction("NSE:ABC", "BONUS", date(2026, 8, 1), 1, 1)
    assert split_adjustment_factor(split) == 0.2
    assert split_adjustment_factor(bonus) == 0.5


def test_adjustment_engine_preserves_raw_prices():
    idx = pd.to_datetime(["2026-05-29", "2026-06-01"])
    frame = pd.DataFrame({
        "open": [500.0, 100.0], "high": [510.0, 105.0], "low": [490.0, 95.0],
        "close": [500.0, 100.0], "volume": [1000.0, 5000.0]
    }, index=idx)
    action = CorporateAction("NSE:ABC", "SPLIT", date(2026, 6, 1), 1, 5)
    out = adjust_ohlcv_for_actions(frame, [action]).frame
    assert out.iloc[0]["close"] == 100.0
    assert out.iloc[0]["volume"] == 5000.0
    assert out.iloc[0]["raw_close"] == 500.0
    assert out.iloc[1]["close"] == 100.0


def test_trade_engine_gap_fill_recalculates_stop_and_size():
    cfg = load_strategy_config()
    p = TradePosition("NSE:ABC", date(2026, 10, 1), 100.0, 95.0, 1_000_000)
    activate_entry(p)
    process_entry_bar(p, date(2026, 10, 2), open_=103.0, high=105.0, cfg=cfg)
    assert p.state == TradeState.FILLED
    assert p.entry == 103.0
    assert p.stop is not None and p.stop < p.entry
    assert p.shares > 0


def test_pending_entry_expires_after_three_trading_bars():
    cfg = load_strategy_config()
    p = TradePosition("NSE:ABC", date(2026, 10, 1), 100.0, 95.0, 1_000_000)
    activate_entry(p)
    for d in (date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)):
        process_entry_bar(p, d, open_=98.0, high=99.0, cfg=cfg)
        assert p.state == TradeState.PENDING_ENTRY
    process_entry_bar(p, date(2026, 10, 7), open_=98.0, high=99.0, cfg=cfg)
    assert p.state == TradeState.CANCELLED
    assert p.exit_reason == "entry_window_expired"


def test_trade_engine_leg1_then_leg2_exit_at_next_open_allows_reentry():
    cfg = load_strategy_config()
    p = TradePosition("NSE:ABC", date(2026, 10, 1), 100.0, 95.0, 1_000_000)
    activate_entry(p)
    process_entry_bar(p, date(2026, 10, 2), 100.0, 101.0, cfg)
    process_position_bar(p, date(2026, 10, 3), 103.0, 106.0, 102.0, 105.0, 102.0, 101.0, 98.0)
    assert p.state == TradeState.LEG1_ARMED

    process_position_bar(p, date(2026, 10, 4), 106.0, 107.0, 103.0, 104.0, 103.0, 105.0, 99.0)
    assert p.state == TradeState.PENDING_LEG1_PARTIAL_EXIT
    process_next_open(p, date(2026, 10, 5), 105.0)
    assert p.state == TradeState.LEG2_ACTIVE
    assert p.stop == p.entry

    process_position_bar(p, date(2026, 10, 5), 105.0, 107.0, 101.0, 102.0, 102.5, 103.0, 103.0)
    assert p.state == TradeState.PENDING_LEG2_EXIT
    process_next_open(p, date(2026, 10, 6), 103.0)
    assert p.state == TradeState.CLOSED
    assert p.exit_reason == "leg2_ema21"
    assert reentry_allowed(p) is True
