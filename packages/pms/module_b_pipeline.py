from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from packages.data_layer.corporate_actions import adjust_ohlcv_for_actions
from packages.data_layer.features import candles_to_frame, compute_features
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
        start = as_of - timedelta(days=lookback_days * 2)
        rows = self.prices.daily_prices(instrument_id, start, as_of, preferred_source=None)
        by_date: dict[date, Candle] = {}
        for row in rows:
            candle = Candle(
                instrument_id=instrument_id,
                timestamp=datetime.combine(row["trade_date"], datetime.min.time()),
                open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
                volume=int(row["volume"]), source=row["source"], adjusted=False,
            )
            existing = by_date.get(candle.timestamp.date())
            if existing is None or candle.source == "angel_one":
                by_date[candle.timestamp.date()] = candle
        raw = [by_date[d] for d in sorted(by_date)]
        if not raw:
            return []

        actions = self.reference.corporate_actions_between(instrument_id, start, as_of)
        if not actions:
            return raw
        adjusted = adjust_ohlcv_for_actions(candles_to_frame(raw), actions).frame
        return [
            Candle(
                instrument_id=instrument_id,
                timestamp=idx.to_pydatetime(),
                open=Decimal(str(row.open)), high=Decimal(str(row.high)),
                low=Decimal(str(row.low)), close=Decimal(str(row.close)),
                volume=int(round(row.volume)), source="adjusted", adjusted=True,
            )
            for idx, row in adjusted.iterrows()
        ]

    def _trading_dates(self, start: date, sessions: int) -> list[date]:
        """Return exchange-wide trading dates, never instrument-specific future bars.

        The daily_prices warehouse is used only as a market calendar: a date is a
        trading session when the market has any stored NSE bars. No future price or
        volume value is read into the decision.
        """
        if sessions <= 0:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select distinct p.trade_date
                    from daily_prices p
                    join instruments i on i.id=p.instrument_id
                    where i.exchange='NSE'
                      and p.trade_date>=:start
                      and p.trade_date<=:end
                    order by p.trade_date
                    limit :limit
                    """
                ),
                {
                    "start": start,
                    "end": start + timedelta(days=max(14, sessions * 4)),
                    "limit": sessions,
                },
            ).scalars().all()
        return list(rows)

    def _latest_setup1_signal(self, features, as_of: date) -> dict[str, Any] | None:
        t = self.cfg["technical"]
        tracker = RallyTracker()
        latest_signal: dict[str, Any] | None = None
        frame = features.dropna(subset=["ema21"])
        for i in range(1, len(frame)):
            row, prev = frame.iloc[i], frame.iloc[i - 1]
            bar = Bar(
                date=str(frame.index[i].date()), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
                ema21=float(row["ema21"]),
            )
            baseline = float(frame.iloc[max(0, i - 50):i]["volume"].mean()) if i > 0 else 0.0
            tracker = advance_rally(tracker, bar, float(prev["close"]), float(prev["ema21"]), baseline, self.cfg)
            if tracker.state == RallyState.SIGNAL_CREATED:
                signal_date = date.fromisoformat(str(tracker.signal_date))
                if signal_date <= as_of:
                    latest_signal = {
                        "signal_date": tracker.signal_date,
                        "signal_high": tracker.signal_high,
                        "signal_low": tracker.signal_low,
                        "rally_start": tracker.start_date,
                        "rally_high_date": tracker.highest_high_date,
                        "rally_pct": round(tracker.rally_pct(), 4),
                        "velocity": round(tracker.velocity(), 4),
                        "entry_window_days": int(t["entry_window_days"]),
                    }
                tracker = RallyTracker()
        return latest_signal

    def evaluate(self, instrument_id: str, as_of: date, *, require_module_a: bool = True) -> ModuleBResult:
        t = self.cfg["technical"]
        rejected: list[str] = []
        candles = self._candles(instrument_id, as_of)
        if len(candles) < int(t["min_history_days"]):
            return ModuleBResult(instrument_id, as_of, False, ("insufficient_history",), None)

        if require_module_a:
            _, a_decision = self.module_a.evaluate(
                instrument_id, datetime.combine(as_of, datetime.max.time()).replace(microsecond=0)
            )
            if not a_decision.eligible:
                rejected.append("module_a_ineligible")

        features = compute_features(candles).data
        latest = features.iloc[-1]
        if float(latest["close"]) <= float(t["min_price"]): rejected.append("price")
        if float(latest.get("avg_volume20", 0) or 0) <= float(t["min_avg_volume_20d"]): rejected.append("avg_volume_20d")
        if float(latest.get("worst_volume20", 0) or 0) <= float(t["min_worst_day_volume_20d"]): rejected.append("worst_volume_20d")
        turnover_cr = float(latest.get("avg_turnover20", 0) or 0) / 10_000_000.0
        if turnover_cr < float(t["min_avg_turnover_20d_cr"]): rejected.append("turnover_low")
        if turnover_cr > float(t["max_avg_turnover_20d_cr"]): rejected.append("turnover_high")
        if float(latest.get("adr20_pct", 0) or 0) < float(t["min_adr_20d_pct"]): rejected.append("adr")
        high52 = float(latest.get("high_52w", 0) or 0); close = float(latest["close"])
        distance_pct = ((high52 - close) / high52 * 100.0) if high52 > 0 else 100.0
        if distance_pct > float(t["max_distance_from_52w_high_pct"]): rejected.append("distance_52w_high")
        if self.reference.is_index_member(instrument_id, "NIFTY50", as_of): rejected.append("nifty50_excluded")

        blackout_n = int(t["earnings_blackout_trading_days"])
        trading_dates = self._trading_dates(as_of, blackout_n + 1)
        if len(trading_dates) < blackout_n + 1:
            # A future blackout cannot be verified without a market calendar; fail closed.
            rejected.append("earnings_calendar_market_dates_unavailable")
        elif self.reference.earnings_within_trading_window(instrument_id, as_of, trading_dates):
            rejected.append("earnings_blackout")

        if rejected:
            return ModuleBResult(instrument_id, as_of, False, tuple(dict.fromkeys(rejected)), None)
        return ModuleBResult(instrument_id, as_of, True, (), self._latest_setup1_signal(features, as_of))
