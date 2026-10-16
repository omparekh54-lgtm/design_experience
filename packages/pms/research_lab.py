from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Iterable

from packages.pms.config import load_strategy_config
from packages.pms.database_backtest import DatabaseBacktestStrategy
from packages.pms.research import DEFAULT_VARIANTS, ResearchVariant, TradeOutcome, calculate_performance, run_robustness_grid


def _trade_outcomes(result) -> list[TradeOutcome]:
    open_lots: dict[str, list[dict]] = {}
    outcomes: list[TradeOutcome] = []
    for fill in result.fills:
        lots = open_lots.setdefault(fill.instrument_id, [])
        if fill.side == "BUY":
            lots.append({"qty": fill.quantity, "price": fill.price, "date": fill.trade_date, "cost": fill.costs.total})
            continue
        remaining = fill.quantity
        sell_cost_remaining = fill.costs.total
        while remaining > 0 and lots:
            lot = lots[0]
            qty = min(remaining, lot["qty"])
            buy_cost = lot["cost"] * (qty / lot["qty"]) if lot["qty"] else 0.0
            sell_cost = sell_cost_remaining * (qty / remaining) if remaining else 0.0
            pnl = (fill.price - lot["price"]) * qty - buy_cost - sell_cost
            capital = lot["price"] * qty + buy_cost
            outcomes.append(TradeOutcome(pnl=pnl, capital_at_entry=capital, holding_days=(fill.trade_date - lot["date"]).days))
            lot["qty"] -= qty; remaining -= qty
            if lot["qty"] <= 0:
                lots.pop(0)
    return outcomes


class ResearchLab:
    def __init__(self, engine, symbols: list[str], start: date, end: date, initial_capital: float | None = None):
        self.engine = engine
        self.symbols = symbols
        self.start = start
        self.end = end
        self.initial_capital = initial_capital
        self.base_config = load_strategy_config()

    def run_variant(self, cfg: dict, variant: ResearchVariant) -> dict:
        strategy = DatabaseBacktestStrategy(self.engine, self.symbols, variant=variant, cfg=cfg)
        result = strategy.run(self.start, self.end, initial_capital=self.initial_capital)
        curve = [value for _, value in result.equity_curve]
        ending_equity = curve[-1] if curve else result.ending_cash
        metrics = calculate_performance(
            initial_capital=result.initial_capital,
            ending_equity=ending_equity,
            equity_curve=curve or [result.initial_capital, ending_equity],
            trades=_trade_outcomes(result),
            years=max((self.end - self.start).days / 365.25, 1 / 365.25),
        )
        payload = asdict(metrics)
        payload.update({
            "variant": variant.name,
            "gross_pnl": result.realized_gross_pnl,
            "costs": result.total_costs,
            "net_realized_pnl": result.realized_net_pnl,
            "fills": len(result.fills),
            "closed_trades_engine": result.closed_trades,
        })
        return payload

    def run_all_variants(self, variants: Iterable[ResearchVariant] = DEFAULT_VARIANTS) -> dict[str, dict]:
        return {variant.name: self.run_variant(self.base_config, variant) for variant in variants}

    def run_robustness(self, variant: ResearchVariant | None = None):
        variant = variant or ResearchVariant("FULL_A_B_C_E")
        return run_robustness_grid(self.base_config, self.run_variant, variant)
