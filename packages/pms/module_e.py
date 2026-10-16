from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegimeInputs:
    midcap_close: float
    midcap_dma50: float
    midcap_dma200: float
    midcap_dma200_slope_pct: float
    breadth_above_50dma_pct: float
    breadth_above_21ema_pct: float
    advance_decline_10d: float
    new_highs_pct: float
    new_lows_pct: float
    india_vix: float
    india_vix_5d_change_pct: float
    strategy_expectancy_r: float | None = None
    valid_rallies_pct: float | None = None
    closed_trades: int = 0


@dataclass(frozen=True)
class RegimeDecision:
    score: float
    raw_score: float
    band: str
    max_open_trades: int
    hard_override: bool
    components: dict[str, float] = field(default_factory=dict)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _band(score: float, cfg: dict) -> tuple[str, int]:
    r = cfg["regime"]
    if score < r["defensive_below"]:
        return "DEFENSIVE", 0
    if score < r["weak_below"]:
        return "WEAK", 2
    if score < r["neutral_below"]:
        return "NEUTRAL", 4
    if score < r["healthy_below"]:
        return "HEALTHY", 6
    return "STRONG", 7


def calculate_regime_score(inputs: RegimeInputs) -> tuple[float, dict[str, float]]:
    """Calculate the Module E 0-100 score using the documented 30/25/15/15/15 weights."""
    trend_norm = 0.0
    trend_norm += 35.0 if inputs.midcap_close > inputs.midcap_dma50 else 0.0
    trend_norm += 45.0 if inputs.midcap_close > inputs.midcap_dma200 else 0.0
    trend_norm += 20.0 if inputs.midcap_dma200_slope_pct > 0 else 0.0

    breadth50 = _clamp(inputs.breadth_above_50dma_pct)
    breadth21 = _clamp(inputs.breadth_above_21ema_pct)
    # A/D ratio 1 is neutral; >=1.5 strong; <=0.5 weak.
    ad_norm = _clamp((inputs.advance_decline_10d - 0.5) / 1.0 * 100.0)
    breadth_norm = breadth50 * 0.4 + breadth21 * 0.4 + ad_norm * 0.2

    net_highs = inputs.new_highs_pct - inputs.new_lows_pct
    highs_norm = _clamp(50.0 + net_highs * 5.0)

    # Lower/falling VIX is healthier. These are scoring transforms, not trading cutoffs.
    level_norm = _clamp((30.0 - inputs.india_vix) / 20.0 * 100.0)
    direction_norm = _clamp(50.0 - inputs.india_vix_5d_change_pct * 3.0)
    volatility_norm = level_norm * 0.7 + direction_norm * 0.3

    if inputs.closed_trades < 10:
        feedback_norm = 50.0
    else:
        expectancy_norm = _clamp(50.0 + (inputs.strategy_expectancy_r or 0.0) * 50.0)
        rallies_norm = _clamp(inputs.valid_rallies_pct if inputs.valid_rallies_pct is not None else 50.0)
        feedback_norm = expectancy_norm * 0.65 + rallies_norm * 0.35

    components = {
        "trend": round(trend_norm / 100.0 * 30.0, 2),
        "breadth": round(breadth_norm / 100.0 * 25.0, 2),
        "highs_lows": round(highs_norm / 100.0 * 15.0, 2),
        "volatility": round(volatility_norm / 100.0 * 15.0, 2),
        "strategy_feedback": round(feedback_norm / 100.0 * 15.0, 2),
    }
    return round(sum(components.values()), 2), components


def decide_regime(
    score: float,
    nifty_midcap_400_above_200dma: bool,
    breadth_above_50dma_pct: float,
    cfg: dict,
    *,
    raw_score: float | None = None,
    components: dict[str, float] | None = None,
) -> RegimeDecision:
    if not nifty_midcap_400_above_200dma and breadth_above_50dma_pct < 30:
        return RegimeDecision(round(score, 2), round(raw_score if raw_score is not None else score, 2), "DEFENSIVE", 0, True, components or {})
    band, capacity = _band(score, cfg)
    return RegimeDecision(round(score, 2), round(raw_score if raw_score is not None else score, 2), band, capacity, False, components or {})


@dataclass
class RegimeTracker:
    """3-day smoothing with immediate downgrades and 2-close upgrade confirmation."""
    raw_scores: list[float] = field(default_factory=list)
    current: RegimeDecision | None = None
    upgrade_candidate: str | None = None
    upgrade_closes: int = 0

    def update(self, inputs: RegimeInputs, cfg: dict) -> RegimeDecision:
        raw, components = calculate_regime_score(inputs)
        self.raw_scores.append(raw)
        self.raw_scores = self.raw_scores[-3:]
        smoothed = sum(self.raw_scores) / len(self.raw_scores)
        proposed = decide_regime(
            smoothed,
            inputs.midcap_close > inputs.midcap_dma200,
            inputs.breadth_above_50dma_pct,
            cfg,
            raw_score=raw,
            components=components,
        )
        if self.current is None or proposed.hard_override:
            self.current = proposed
            self.upgrade_candidate = None
            self.upgrade_closes = 0
            return proposed

        order = {"DEFENSIVE": 0, "WEAK": 1, "NEUTRAL": 2, "HEALTHY": 3, "STRONG": 4}
        if order[proposed.band] < order[self.current.band]:
            self.current = proposed
            self.upgrade_candidate = None
            self.upgrade_closes = 0
        elif order[proposed.band] > order[self.current.band]:
            if self.upgrade_candidate == proposed.band:
                self.upgrade_closes += 1
            else:
                self.upgrade_candidate = proposed.band
                self.upgrade_closes = 1
            if self.upgrade_closes >= 2:
                self.current = proposed
                self.upgrade_candidate = None
                self.upgrade_closes = 0
            else:
                self.current = RegimeDecision(smoothed, raw, self.current.band, self.current.max_open_trades, False, components)
        else:
            self.current = proposed
            self.upgrade_candidate = None
            self.upgrade_closes = 0
        return self.current
