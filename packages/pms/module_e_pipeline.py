from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import text

from packages.data_layer.features import compute_features
from packages.data_layer.queries import PointInTimeQueries
from packages.data_layer.reference_data import ReferenceDataQueries
from packages.pms.config import load_strategy_config
from packages.pms.module_b_pipeline import ModuleBPipeline
from packages.pms.module_e import RegimeInputs, RegimeTracker


class ModuleEPipeline:
    """Build and persist daily Module E regime decisions from stored point-in-time market data."""

    def __init__(self, engine):
        self.engine = engine
        self.cfg = load_strategy_config()
        self.prices = PointInTimeQueries(engine)
        self.reference = ReferenceDataQueries(engine)
        self._tracker = RegimeTracker()

    def _index_features(self, instrument_id: str, as_of: date):
        rows = self.prices.daily_prices(instrument_id, as_of - timedelta(days=400), as_of, preferred_source=None)
        if not rows:
            raise ValueError(f"missing index history: {instrument_id}")
        pipeline = ModuleBPipeline(self.engine)
        candles = pipeline._candles(instrument_id, as_of, 300)
        frame = compute_features(candles).data.dropna(subset=["dma50", "dma200"])
        if frame.empty:
            raise ValueError(f"insufficient index history: {instrument_id}")
        return frame

    def _breadth(self, as_of: date) -> tuple[float, float, float, float, float]:
        universe = self.reference.universe_as_of(as_of)
        above50 = above21 = adv = dec = highs = lows = valid = 0
        for instrument_id in universe:
            try:
                candles = ModuleBPipeline(self.engine)._candles(instrument_id, as_of, 300)
                frame = compute_features(candles).data
                if len(frame) < 50:
                    continue
                latest = frame.iloc[-1]
                valid += 1
                above50 += int(float(latest["close"]) > float(latest["dma50"]))
                above21 += int(float(latest["close"]) > float(latest["ema21"]))
                if len(frame) >= 11:
                    change = float(latest["close"]) - float(frame.iloc[-11]["close"])
                    adv += int(change > 0); dec += int(change < 0)
                highs += int(float(latest["high"]) >= float(latest["high_52w"]))
                lows += int(float(latest["low"]) <= float(latest["low_52w"]))
            except Exception:
                continue
        if not valid:
            raise ValueError("no breadth universe data")
        return (
            above50 / valid * 100.0,
            above21 / valid * 100.0,
            adv / max(dec, 1),
            highs / valid * 100.0,
            lows / valid * 100.0,
        )

    def _strategy_feedback(self) -> tuple[float | None, int]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("select realized_pnl from trades where state='CLOSED' order by updated_at desc limit 15")).scalars().all()
        if len(rows) < 10:
            return None, len(rows)
        return sum(float(x or 0) for x in rows) / max(len(rows), 1) / 1000.0, len(rows)

    def evaluate(self, as_of: date, *, midcap_id: str = "NSE:NIFTYMIDCAP400", vix_id: str = "NSE:INDIAVIX", persist: bool = True):
        mid = self._index_features(midcap_id, as_of)
        last = mid.iloc[-1]
        slope = (float(last["dma200"]) / float(mid.iloc[-6]["dma200"]) - 1) * 100 if len(mid) >= 6 else 0.0
        vix_rows = self.prices.daily_prices(vix_id, as_of - timedelta(days=10), as_of, preferred_source=None)
        if len(vix_rows) < 6:
            raise ValueError("missing India VIX history")
        vix_now = float(vix_rows[-1]["close"]); vix_old = float(vix_rows[-6]["close"])
        b50, b21, ad, highs, lows = self._breadth(as_of)
        expectancy, closed = self._strategy_feedback()
        inputs = RegimeInputs(
            midcap_close=float(last["close"]), midcap_dma50=float(last["dma50"]), midcap_dma200=float(last["dma200"]),
            midcap_dma200_slope_pct=slope, breadth_above_50dma_pct=b50, breadth_above_21ema_pct=b21,
            advance_decline_10d=ad, new_highs_pct=highs, new_lows_pct=lows, india_vix=vix_now,
            india_vix_5d_change_pct=(vix_now / vix_old - 1) * 100, strategy_expectancy_r=expectancy,
            valid_rallies_pct=None, closed_trades=closed,
        )
        decision = self._tracker.update(inputs, self.cfg)
        if persist:
            with self.engine.begin() as conn:
                conn.execute(text("""
                    insert into market_regime_history
                    (trade_date, raw_score, smoothed_score, band, max_open_trades, hard_override, components, inputs, strategy_version)
                    values (:d,:raw,:score,:band,:cap,:hard,cast(:components as jsonb),cast(:inputs as jsonb),:version)
                    on conflict (trade_date) do update set raw_score=excluded.raw_score, smoothed_score=excluded.smoothed_score,
                    band=excluded.band, max_open_trades=excluded.max_open_trades, hard_override=excluded.hard_override,
                    components=excluded.components, inputs=excluded.inputs, strategy_version=excluded.strategy_version
                """), {"d":as_of,"raw":decision.raw_score,"score":decision.score,"band":decision.band,"cap":decision.max_open_trades,
                       "hard":decision.hard_override,"components":json.dumps(decision.components),"inputs":json.dumps(inputs.__dict__),"version":self.cfg["version"]})
        return inputs, decision
