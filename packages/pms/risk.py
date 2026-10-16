from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PositionSize:
    shares: int
    stop: float
    rupee_risk: float
    position_value: float
    position_pct: float


def compute_stop(entry: float, signal_low: float, raw_stop_buffer_pct: float, max_stop_risk_pct: float) -> float:
    raw = signal_low * (1 - raw_stop_buffer_pct / 100)
    capped_floor = entry * (1 - max_stop_risk_pct / 100)
    return round(max(raw, capped_floor), 4)


def size_position(
    capital: float,
    entry: float,
    signal_low: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
    raw_stop_buffer_pct: float,
    max_stop_risk_pct: float,
) -> PositionSize:
    if capital <= 0 or entry <= 0 or signal_low <= 0:
        raise ValueError("capital, entry and signal_low must be positive")

    stop = compute_stop(entry, signal_low, raw_stop_buffer_pct, max_stop_risk_pct)
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        raise ValueError("stop must be below entry")

    risk_budget = capital * risk_per_trade_pct / 100
    by_risk = math.floor(risk_budget / risk_per_share)
    by_cap = math.floor((capital * max_position_pct / 100) / entry)
    shares = max(0, min(by_risk, by_cap))

    value = shares * entry
    rupee_risk = shares * risk_per_share
    return PositionSize(
        shares=shares,
        stop=stop,
        rupee_risk=round(rupee_risk, 2),
        position_value=round(value, 2),
        position_pct=round((value / capital) * 100, 4),
    )
