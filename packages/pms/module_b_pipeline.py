from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from packages.data_layer.features import compute_features
from packages.data_layer.models import Candle
from packages.data_layer.queries import PointInTimeQueries
from packages.data_layer.reference_data import ReferenceDataQueries
from packages.pms.config import load_strategy_config
from packages.pms.module_a_pipeline import ModuleAPipeline
from packages.pms.module_b import Bar, RallyState, RallyTracker, advance_rally


@dataclass(frozen=True)
class ModuleBResult:
    instrument_id: str
    as_of: date
    eligible_universe: bool
    rejected_reasons: tuple[str, ...]
    signal: dict[str, Any] | None


class ModuleBPipeline:
    def __init__(self, engine):
        self.engine = engine
        self.prices = PointInTimeQueries(engine)
        self.reference = ReferenceDataQueries(engine)
        self.module_a = ModuleAPipeline(engine)
        self.cfg = load_strategy_config()

    def _candles(self, instrument_id: str, as_of: date, lookback_days: int = 500) -> list[Candle]:
        rows = self.prices.daily_prices(
            instrument_id,
            as_of - timedelta(days=lookback_days * 2),
            as_of,
            preferred_source=None,
        )
        candles: list[Candle] = []
        for row in rows:
            candles.append(Candle(
                instrument_id=instrument_id,
                timestamp=datetime.combine(row["trade_date"], datetime.min.time()),
                open=row["open"], high=row["high"], low=row["low"], close=row["close"],
                volume=int(row["volume"]), source=row["source"], adjusted=False,
            ))
        # Keep one provider row per date, preferring angel_one if duplicated.
        by_date: dict[date, Candle] = {}
        for c in candles:
            existing = by_date.get(c.timestamp.date())
            if existing is None or c.source == "angel_one":
                by_date[c.timestamp.date()] = c
        return [by_date[d] for d in sorted(by_date)]

    def evaluate(self, instrument_id: str, as_of: date) -> ModuleBResult:
        t = self.cfg["technical"]
        rejected: list[str] = []
        candles = self._candles(instrument_id, as_of)
        if len(candles) < int(t["min_history_days"]):
            return ModuleBResult(instrument_id, as_of, False, ("insufficient_history",), None)

        a_snapshot, a_decision = self.module_a.evaluate(
            instrument_id, datetime.combine(as_of, datetime.max.time()).replace(microsecond=0)
        )
        if not a_decision.eligible:
            rejected.append("module_a_ineligible")

        features = compute_features(candles).data
        latest = features.iloc[-1]
        if float(latest["close"]) <= float(t["min_price"]):
            rejected.append("price")
        if float(latest.get("avg_volume20", 0) or 0) <= float(t["min_avg_volume_20d"]):
            rejected.append("avg_volume_20d")
        if float(latest.get("worst_volume20", 0) or 0) <= float(t["min_worst_day_volume_20d"]):
            rejected.append("worst_volume_20d")
        turnover_cr = float(latest.get("avg_turnover20", 0) or 0) / 10_000_000.0
        if turnover_cr < float(t["min_avg_turnover_20d_cr"]):
            rejected.append("turnover_low")
        if turnover_cr > float(t["max_avg_turnover_20d_cr"]):
            rejected.append("turnover_high")
        if float(latest.get("adr20_pct", 0) or 0) < float(t["min_adr_20d_pct"]):
            rejected.append("adr")
        high52 = float(latest.get("high_52w", 0) or 0)
        close = float(latest["close"])
        distance_pct = ((high52 - close) / high52 * 100.0) if high52 > 0 else 100.0
        if distance_pct > float(t["max_distance_from_52w_high_pct"]):
            rejected.append("distance_52w_high")
        if self.reference.is_index_member(instrument_id, "NIFTY50", as_of):
            rejected.append("nifty50_excluded")

        # Result blackout: no new entries in the next configured trading sessions.
        future_rows = self.prices.daily_prices(
            instrument_id, as_of, as_of + timedelta(days=10), preferred_source=None
        )
        trading_dates = sorted({r["trade_date"] for r in future_rows if r["trade_date"] >= as_of})
        blackout_n = int(t["earnings_blackout_trading_days"])
        if self.reference.earnings_within_trading_window(instrument_id, as_of, trading_dates[: blackout_n + 1]):
            rejected.append("earnings_blackout")

        if rejected:
            return ModuleBResult(instrument_id, as_of, False, tuple(dict.fromkeys(rejected)), None)

        tracker = RallyTracker()
        signal: dict[str, Any] | None = None
        frame = features.dropna(subset=["ema21"])
        for i in range(1, len(frame)):
            row = frame.iloc[i]
            prev = frame.iloc[i - 1]
            bar = Bar(
                date=str(frame.index[i].date()),
                open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]), volume=float(row["volume"]), ema21=float(row["ema21"]),
            )
            baseline = float(frame.iloc[max(0, i - 50):i]["volume"].mean()) if i > 0 else 0.0
            tracker = advance_rally(
                tracker, bar, float(prev["close"]), float(prev["ema21"]), baseline, self.cfg
            )
            if tracker.state == RallyState.SIGNAL_CREATED:
                signal_date = date.fromisoformat(str(tracker.signal_date))
                if signal_date <= as_of:
                    signal = {
                        "signal_date": tracker.signal_date,
                        "signal_high": tracker.signal_high,
                        "signal_low": tracker.signal_low,
                        "rally_start": tracker.start_date,
                        "rally_high_date": tracker.highest_high_date,
                        "rally_pct": round(tracker.rally_pct(), 4),
                        "velocity": round(tracker.velocity(), 4),
                        "entry_window_days": int(t["entry_window_days"]),
                    }
                break

        return ModuleBResult(instrument_id, as_of, True, (), signal)
