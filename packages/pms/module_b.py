from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RallyState(str, Enum):
    BELOW_21EMA = "BELOW_21EMA"
    RALLY_TRACKING = "RALLY_TRACKING"
    RALLY_VALID = "RALLY_VALID"
    SIGNAL_CREATED = "SIGNAL_CREATED"


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema21: float


@dataclass
class RallyTracker:
    state: RallyState = RallyState.BELOW_21EMA
    start_date: str | None = None
    base: float | None = None
    highest_high: float | None = None
    highest_high_date: str | None = None
    trading_days: int = 0
    cumulative_volume: float = 0.0
    signal_date: str | None = None
    signal_high: float | None = None
    signal_low: float | None = None

    def rally_pct(self) -> float:
        if not self.base or not self.highest_high:
            return 0.0
        return (self.highest_high - self.base) / self.base * 100

    def velocity(self) -> float:
        return self.rally_pct() / max(self.trading_days, 1)


def advance_rally(
    tracker: RallyTracker,
    bar: Bar,
    previous_close: float,
    previous_ema21: float,
    baseline_volume_50d: float,
    cfg: dict,
) -> RallyTracker:
    t = cfg["technical"]

    crossed_above = previous_close <= previous_ema21 and bar.close > bar.ema21

    if tracker.state == RallyState.BELOW_21EMA:
        if crossed_above:
            tracker.state = RallyState.RALLY_TRACKING
            tracker.start_date = bar.date
            tracker.base = bar.low
            tracker.highest_high = bar.high
            tracker.highest_high_date = bar.date
            tracker.trading_days = 1
            tracker.cumulative_volume = bar.volume
        return tracker

    if tracker.state in (RallyState.RALLY_TRACKING, RallyState.RALLY_VALID):
        if bar.close < bar.ema21:
            if tracker.state == RallyState.RALLY_VALID:
                tracker.state = RallyState.SIGNAL_CREATED
                tracker.signal_date = bar.date
                tracker.signal_high = bar.high
                tracker.signal_low = bar.low
            else:
                _reset(tracker)
            return tracker

        tracker.trading_days += 1
        tracker.cumulative_volume += bar.volume
        if tracker.highest_high is None or bar.high > tracker.highest_high:
            tracker.highest_high = bar.high
            tracker.highest_high_date = bar.date

        if tracker.trading_days > t["rally_max_days"]:
            _reset(tracker)
            return tracker

        avg_rally_volume = tracker.cumulative_volume / tracker.trading_days
        volume_ratio = avg_rally_volume / baseline_volume_50d if baseline_volume_50d > 0 else 0
        if (
            tracker.rally_pct() >= t["rally_min_pct"]
            and tracker.velocity() >= t["rally_min_velocity_pct_per_day"]
            and volume_ratio >= t["rally_min_volume_ratio"]
        ):
            tracker.state = RallyState.RALLY_VALID

    return tracker


def _reset(tracker: RallyTracker) -> None:
    tracker.state = RallyState.BELOW_21EMA
    tracker.start_date = None
    tracker.base = None
    tracker.highest_high = None
    tracker.highest_high_date = None
    tracker.trading_days = 0
    tracker.cumulative_volume = 0.0
    tracker.signal_date = None
    tracker.signal_high = None
    tracker.signal_low = None
