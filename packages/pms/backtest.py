from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Iterable

from packages.pms.config import load_strategy_config
from packages.pms.costs import CostBreakdown, estimate_delivery_costs
from packages.pms.trade_engine import (
    TradePosition,
    TradeState,
    activate_entry,
    create_reentry,
    process_entry_bar,
    process_next_open,
    process_position_bar,
    reentry_allowed,
)


class EventType(str, Enum):
    MARKET_OPEN = "MARKET_OPEN"
    BAR = "BAR"
    SIGNAL = "SIGNAL"
    ENTRY_FILL = "ENTRY_FILL"
    EXIT_FILL = "EXIT_FILL"
    POSITION_STATE = "POSITION_STATE"
    REENTRY_SIGNAL = "REENTRY_SIGNAL"
    MARKET_CLOSE = "MARKET_CLOSE"


@dataclass(frozen=True)
class MarketBar:
    instrument_id: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    ema9: float
    previous_ema9: float
    previous_ema21: float


@dataclass(frozen=True)
class Signal:
    instrument_id: str
    signal_date: date
    signal_high: float
    signal_low: float


@dataclass(frozen=True)
class Fill:
    instrument_id: str
    trade_date: date
    side: str
    price: float
    quantity: int
    reason: str
    costs: CostBreakdown


@dataclass(frozen=True)
class BacktestEvent:
    event_type: EventType
    trade_date: date
    instrument_id: str | None = None
    payload: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    initial_capital: float
    ending_cash: float
    realized_gross_pnl: float
    total_costs: float
    realized_net_pnl: float
    return_pct: float
    fills: list[Fill]
    events: list[BacktestEvent]
    closed_trades: int


SignalProvider = Callable[[date], Iterable[Signal]]
EligibilityProvider = Callable[[str, date], bool]


class EventDrivenBacktester:
    """Daily event-driven backtester using the production trade state machine.

    Signals are generated after close. Buy-stop orders begin on the next supplied
    trading bar. EMA exits are generated at the close and executed next open;
    active stops remain intraday. A Leg-2 EMA21 exit automatically seeds the
    rule-defined re-entry signal when the stock remains Module-A eligible.
    """

    def __init__(self, initial_capital: float | None = None, cfg: dict | None = None):
        self.cfg = cfg or load_strategy_config()
        bt = self.cfg["backtest"]
        self.initial_capital = float(initial_capital or bt["initial_capital"])
        self.cash = self.initial_capital
        self.positions: dict[str, TradePosition] = {}
        self.fills: list[Fill] = []
        self.events: list[BacktestEvent] = []
        self.total_costs = 0.0
        self.realized_gross_pnl = 0.0
        self.closed_trade_count = 0

    def _active_count(self) -> int:
        terminal = {TradeState.CLOSED, TradeState.CANCELLED}
        return sum(1 for p in self.positions.values() if p.state not in terminal)

    def _record_fill(self, *, d: date, instrument_id: str, side: str, price: float, qty: int, reason: str) -> None:
        if qty <= 0:
            return
        costs = estimate_delivery_costs(
            trade_date=d,
            side=side,
            price=price,
            quantity=qty,
            cfg=self.cfg["backtest"],
        )
        self.total_costs += costs.total
        turnover = price * qty
        if side == "BUY":
            self.cash -= turnover + costs.total
        else:
            self.cash += turnover - costs.total
        self.fills.append(Fill(instrument_id, d, side, price, qty, reason, costs))
        self.events.append(BacktestEvent(
            EventType.ENTRY_FILL if side == "BUY" else EventType.EXIT_FILL,
            d,
            instrument_id,
            {"reason": reason, "price": price, "quantity": qty, "costs": costs.total},
        ))

    def _apply_open_execution(
        self,
        position: TradePosition,
        bar: MarketBar,
        eligibility_provider: EligibilityProvider,
    ) -> TradePosition | None:
        before_state = position.state
        before_remaining = position.remaining_shares
        before_pnl = position.realized_pnl
        process_next_open(position, bar.trade_date, bar.open)
        replacement: TradePosition | None = None
        if position.state != before_state:
            sold = max(0, before_remaining - position.remaining_shares)
            if sold:
                self._record_fill(
                    d=bar.trade_date,
                    instrument_id=position.instrument_id,
                    side="SELL",
                    price=bar.open,
                    qty=sold,
                    reason=position.exit_reason or "ema_exit",
                )
            self.realized_gross_pnl += position.realized_pnl - before_pnl
            self.events.append(BacktestEvent(
                EventType.POSITION_STATE,
                bar.trade_date,
                position.instrument_id,
                {"from": before_state.value, "to": position.state.value},
            ))
            if position.state == TradeState.CLOSED:
                self.closed_trade_count += 1

            if reentry_allowed(position) and eligibility_provider(position.instrument_id, bar.trade_date):
                replacement = create_reentry(position, max(self.cash, 0.0))
                activate_entry(replacement)
                self.events.append(BacktestEvent(
                    EventType.REENTRY_SIGNAL,
                    bar.trade_date,
                    position.instrument_id,
                    {
                        "signal_date": str(replacement.signal_date),
                        "signal_high": replacement.signal_high,
                        "signal_low": replacement.signal_low,
                    },
                ))
        return replacement

    def _apply_entry(self, position: TradePosition, bar: MarketBar) -> None:
        before = position.state
        process_entry_bar(position, bar.trade_date, bar.open, bar.high, self.cfg)
        if before == TradeState.PENDING_ENTRY and position.state == TradeState.FILLED:
            required = float(position.entry) * position.shares
            if required > self.cash:
                position.state = TradeState.CANCELLED
                position.exit_reason = "insufficient_cash"
                position.log("ENTRY_CANCELLED", date=str(bar.trade_date), reason=position.exit_reason)
                return
            self._record_fill(
                d=bar.trade_date,
                instrument_id=position.instrument_id,
                side="BUY",
                price=float(position.entry),
                qty=position.shares,
                reason="entry",
            )

            if bool(self.cfg["backtest"].get("same_day_stop_after_entry", True)) and bar.low <= float(position.stop):
                before_pnl = position.realized_pnl
                qty = position.remaining_shares
                stop_price = bar.open if bar.open < float(position.stop) else float(position.stop)
                process_position_bar(
                    position,
                    bar.trade_date,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.ema9,
                    bar.previous_ema9,
                    bar.previous_ema21,
                )
                if position.state == TradeState.CLOSED and qty > 0:
                    self._record_fill(
                        d=bar.trade_date,
                        instrument_id=position.instrument_id,
                        side="SELL",
                        price=stop_price,
                        qty=qty,
                        reason="same_day_stop",
                    )
                    self.realized_gross_pnl += position.realized_pnl - before_pnl
                    self.closed_trade_count += 1

    def _apply_bar(self, position: TradePosition, bar: MarketBar) -> None:
        if position.state not in {TradeState.FILLED, TradeState.LEG1_ARMED, TradeState.LEG2_ACTIVE}:
            return
        before_state = position.state
        before_remaining = position.remaining_shares
        before_pnl = position.realized_pnl
        process_position_bar(
            position,
            bar.trade_date,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.ema9,
            bar.previous_ema9,
            bar.previous_ema21,
        )
        if position.state == TradeState.CLOSED and before_remaining > 0:
            last = position.history[-1]
            price = float(last.get("price", bar.close))
            self._record_fill(
                d=bar.trade_date,
                instrument_id=position.instrument_id,
                side="SELL",
                price=price,
                qty=before_remaining,
                reason=position.exit_reason or "exit",
            )
            self.closed_trade_count += 1
        self.realized_gross_pnl += position.realized_pnl - before_pnl
        if position.state != before_state:
            self.events.append(BacktestEvent(
                EventType.POSITION_STATE,
                bar.trade_date,
                position.instrument_id,
                {"from": before_state.value, "to": position.state.value},
            ))

    def run(
        self,
        bars: Iterable[MarketBar],
        signal_provider: SignalProvider,
        eligibility_provider: EligibilityProvider | None = None,
    ) -> BacktestResult:
        eligibility_provider = eligibility_provider or (lambda _instrument, _date: True)
        bars_by_date: dict[date, dict[str, MarketBar]] = {}
        for bar in bars:
            bars_by_date.setdefault(bar.trade_date, {})[bar.instrument_id] = bar

        for d in sorted(bars_by_date):
            daily = bars_by_date[d]
            self.events.append(BacktestEvent(EventType.MARKET_OPEN, d))

            replacements: dict[str, TradePosition] = {}
            for instrument_id, position in list(self.positions.items()):
                bar = daily.get(instrument_id)
                if bar is None:
                    continue
                if position.state in {
                    TradeState.PENDING_LEG1_FULL_EXIT,
                    TradeState.PENDING_LEG1_PARTIAL_EXIT,
                    TradeState.PENDING_LEG2_EXIT,
                }:
                    replacement = self._apply_open_execution(position, bar, eligibility_provider)
                    if replacement is not None:
                        replacements[instrument_id] = replacement
            self.positions.update(replacements)

            for instrument_id, position in list(self.positions.items()):
                bar = daily.get(instrument_id)
                if bar is None:
                    continue
                if position.state == TradeState.PENDING_ENTRY:
                    self._apply_entry(position, bar)
                if position.state in {TradeState.FILLED, TradeState.LEG1_ARMED, TradeState.LEG2_ACTIVE}:
                    self._apply_bar(position, bar)

            self.events.append(BacktestEvent(EventType.MARKET_CLOSE, d))
            for signal in signal_provider(d):
                existing = self.positions.get(signal.instrument_id)
                if existing and existing.state not in {TradeState.CLOSED, TradeState.CANCELLED}:
                    continue
                if self._active_count() >= int(self.cfg["risk"]["max_open_trades"]):
                    continue
                if not eligibility_provider(signal.instrument_id, d):
                    continue
                p = TradePosition(
                    signal.instrument_id,
                    signal.signal_date,
                    signal.signal_high,
                    signal.signal_low,
                    max(self.cash, 0.0),
                )
                activate_entry(p)
                self.positions[signal.instrument_id] = p
                self.events.append(BacktestEvent(
                    EventType.SIGNAL,
                    d,
                    signal.instrument_id,
                    {"signal_high": signal.signal_high, "signal_low": signal.signal_low},
                ))

        net = self.realized_gross_pnl - self.total_costs
        return BacktestResult(
            initial_capital=self.initial_capital,
            ending_cash=round(self.cash, 2),
            realized_gross_pnl=round(self.realized_gross_pnl, 2),
            total_costs=round(self.total_costs, 2),
            realized_net_pnl=round(net, 2),
            return_pct=round(net / self.initial_capital * 100.0, 4),
            fills=self.fills,
            events=self.events,
            closed_trades=self.closed_trade_count,
        )
