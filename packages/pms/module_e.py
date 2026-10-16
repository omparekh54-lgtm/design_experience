from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeDecision:
    score: float
    band: str
    max_open_trades: int
    hard_override: bool


def decide_regime(
    score: float,
    nifty_midcap_400_above_200dma: bool,
    breadth_above_50dma_pct: float,
    cfg: dict,
) -> RegimeDecision:
    if not nifty_midcap_400_above_200dma and breadth_above_50dma_pct < 30:
        return RegimeDecision(score=round(score, 2), band="DEFENSIVE", max_open_trades=0, hard_override=True)

    r = cfg["regime"]
    if score < r["defensive_below"]:
        band, capacity = "DEFENSIVE", 0
    elif score < r["weak_below"]:
        band, capacity = "WEAK", 2
    elif score < r["neutral_below"]:
        band, capacity = "NEUTRAL", 4
    elif score < r["healthy_below"]:
        band, capacity = "HEALTHY", 6
    else:
        band, capacity = "STRONG", 7

    return RegimeDecision(score=round(score, 2), band=band, max_open_trades=capacity, hard_override=False)
