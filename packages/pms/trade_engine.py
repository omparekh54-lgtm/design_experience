from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from packages.pms.risk import compute_stop, size_position


class TradeState(str, Enum):
    SIGNAL_CREATED = "SIGNAL_CREATED"
    PENDING_ENTRY = "PENDING_ENTRY"
    FILLED = "FILLED"
    LEG1_ARMED = "LEG1_ARMED"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    LEG2_ACTIVE = "LEG2_ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


@dataclass
class TradePosition:
    instrument_id: str
    signal_date: date
    signal_high: float
    signal_low: float
    capital: float
    state: TradeState = TradeState.SIGNAL_CREATED
    entry: float | None = None
    stop: float | None = None
    shares: int = 0
    remaining_shares: int = 0
    filled_on: date | None = None
    leg1_armed: bool = False
    realized_pnl: float = 0.0
    exit_reason: str | None = None
    history: list[dict] = field(default_factory=list)

    def log(self, event: str, **payload) -> None:
        self.history.append({"event": event, **payload})


def activate_entry(position: TradePosition) -> TradePosition:
    if position.state != TradeState.SIGNAL_CREATED:
        raise ValueError("entry can only be activated from SIGNAL_CREATED")
    position.state = TradeState.PENDING_ENTRY
    position.log("ENTRY_ACTIVATED", buy_stop=position.signal_high)
    return position


def process_entry_bar(position: TradePosition, bar_date: date, open_: float, high: float, cfg: dict) -> TradePosition:
    if position.state != TradeState.PENDING_ENTRY:
        return position
    window = int(cfg["technical"]["entry_window_days"])
    if (bar_date - position.signal_date).days > window + 4:  # calendar guard; scheduler supplies trading bars
        position.state = TradeState.CANCELLED
        position.exit_reason = "entry_window_expired"
        position.log("ENTRY_CANCELLED", reason=position.exit_reason)
        return position
    if high < position.signal_high:
        return position
    fill = max(open_, position.signal_high)
    r = cfg["risk"]
    sizing = size_position(
        capital=position.capital,
        entry=fill,
        signal_low=position.signal_low,
        risk_per_trade_pct=float(r["risk_per_trade_pct"]),
        max_position_pct=float(r["max_position_pct"]),
        raw_stop_buffer_pct=float(r["raw_stop_buffer_pct"]),
        max_stop_risk_pct=float(r["max_stop_risk_pct"]),
    )
    if sizing.shares < 1:
        position.state = TradeState.CANCELLED
        position.exit_reason = "position_size_zero"
        return position
    position.entry = fill
    position.stop = sizing.stop
    position.shares = sizing.shares
    position.remaining_shares = sizing.shares
    position.filled_on = bar_date
    position.state = TradeState.FILLED
    position.log("FILLED", entry=fill, stop=sizing.stop, shares=sizing.shares)
    return position


def cancel_if_fundamental_ineligible(position: TradePosition) -> TradePosition:
    if position.state in {TradeState.SIGNAL_CREATED, TradeState.PENDING_ENTRY}:
        position.state = TradeState.CANCELLED
        position.exit_reason = "module_a_ineligible"
        position.log("ENTRY_CANCELLED", reason=position.exit_reason)
    return position


def process_position_bar(
    position: TradePosition,
    bar_date: date,
    open_: float,
    high: float,
    low: float,
    close: float,
    ema9: float,
    previous_ema9: float,
    previous_ema21: float,
) -> TradePosition:
    if position.state not in {TradeState.FILLED, TradeState.LEG1_ARMED, TradeState.PARTIAL_EXIT, TradeState.LEG2_ACTIVE}:
        return position
    if position.entry is None or position.stop is None:
        raise ValueError("filled trade missing entry/stop")

    # Initial/breakeven stop. Gaps below stop exit at the open.
    if low <= position.stop:
        exit_price = open_ if open_ < position.stop else position.stop
        pnl = (exit_price - position.entry) * position.remaining_shares
        position.realized_pnl += pnl
        position.remaining_shares = 0
        position.state = TradeState.CLOSED
        position.exit_reason = "stop"
        position.log("CLOSED", date=str(bar_date), price=exit_price, reason="stop", pnl=pnl)
        return position

    if position.state == TradeState.FILLED and close > ema9:
        position.leg1_armed = True
        position.state = TradeState.LEG1_ARMED
        position.log("LEG1_ARMED", date=str(bar_date))

    if position.state == TradeState.LEG1_ARMED and close < previous_ema9:
        if close <= position.entry:
            pnl = (close - position.entry) * position.remaining_shares
            position.realized_pnl += pnl
            position.remaining_shares = 0
            position.state = TradeState.CLOSED
            position.exit_reason = "leg1_full_exit"
            position.log("CLOSED", date=str(bar_date), price=close, reason=position.exit_reason, pnl=pnl)
            return position
        qty = max(1, position.remaining_shares // 2)
        pnl = (close - position.entry) * qty
        position.realized_pnl += pnl
        position.remaining_shares -= qty
        position.stop = position.entry
        position.state = TradeState.LEG2_ACTIVE
        position.log("PARTIAL_EXIT", date=str(bar_date), price=close, shares=qty, pnl=pnl, stop=position.stop)

    if position.state == TradeState.LEG2_ACTIVE:
        if low <= position.entry:
            exit_price = open_ if open_ < position.entry else position.entry
            pnl = (exit_price - position.entry) * position.remaining_shares
            position.realized_pnl += pnl
            position.remaining_shares = 0
            position.state = TradeState.CLOSED
            position.exit_reason = "breakeven_stop"
            position.log("CLOSED", date=str(bar_date), price=exit_price, reason=position.exit_reason, pnl=pnl)
        elif close < previous_ema21:
            pnl = (close - position.entry) * position.remaining_shares
            position.realized_pnl += pnl
            position.remaining_shares = 0
            position.state = TradeState.CLOSED
            position.exit_reason = "leg2_ema21"
            position.log("CLOSED", date=str(bar_date), price=close, reason=position.exit_reason, pnl=pnl)
    return position


def reentry_allowed(position: TradePosition) -> bool:
    return position.state == TradeState.CLOSED and position.exit_reason == "leg2_ema21"
