from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from packages.data_layer.features import compute_features
from packages.pms.module_b_pipeline import ModuleBResult, ModuleBPipeline


class EarningsAnchoredSetup2:
    """Backtest-only Setup 2 from the PMS rules.

    Requires a Module-A Stage-3 catalyst, anchors the base to the pre-result close
    so the gap counts, needs >=15% rally within 30 trading sessions at >=1%/day,
    reaction volume >=2x prior 20-day average, and signals on the first close below
    EMA21 after a valid rally. A setup expires after 60 trading sessions.
    """

    def __init__(self, pipeline: ModuleBPipeline):
        self.pipeline = pipeline

    def evaluate(self, instrument_id: str, as_of: date, *, require_module_a: bool = True) -> ModuleBResult:
        candles = self.pipeline._candles(instrument_id, as_of, 800)
        if len(candles) < 252:
            return ModuleBResult(instrument_id, as_of, False, ("insufficient_history",), None)
        frame = compute_features(candles).data.dropna(subset=["ema21"])
        history = self.pipeline.prices.financial_history_as_of(
            instrument_id,
            datetime.combine(as_of, datetime.max.time()).replace(microsecond=0),
            limit_periods=24,
        )
        quarterly = [x for x in history if str(x.get("filing_kind") or "quarter").lower() not in {"annual", "year", "yearly"}]
        latest_signal = None

        for filing in sorted(quarterly, key=lambda x: x["filed_at"]):
            filed_date = filing["filed_at"].date()
            if filed_date > as_of:
                continue
            if require_module_a:
                try:
                    _, a_decision = self.pipeline.module_a.evaluate(instrument_id, filing["filed_at"])
                except Exception:
                    continue
                if not a_decision.triggers:
                    continue

            after = frame[frame.index.date > filed_date]
            if after.empty:
                continue
            first_idx = after.index[0]
            first_pos = frame.index.get_loc(first_idx)
            if first_pos < 20:
                continue
            pre_close = float(frame.iloc[first_pos - 1]["close"])
            prior20_volume = float(frame.iloc[first_pos - 20:first_pos]["volume"].mean())
            reaction_volume = float(frame.iloc[first_pos]["volume"])
            if prior20_volume <= 0 or reaction_volume / prior20_volume < 2.0:
                continue

            window = frame.iloc[first_pos:first_pos + 60]
            highest = pre_close
            highest_day = 0
            valid = False
            for j, (_, row) in enumerate(window.iterrows(), start=1):
                if j <= 30 and float(row["high"]) > highest:
                    highest = float(row["high"]); highest_day = j
                rally_pct = (highest / pre_close - 1.0) * 100.0
                velocity = rally_pct / max(highest_day, 1)
                if j <= 30 and rally_pct >= 15.0 and velocity >= 1.0:
                    valid = True
                if not valid and float(row["close"]) < float(row["ema21"]):
                    break
                if valid and float(row["close"]) < float(row["ema21"]):
                    signal_date = row.name.date()
                    if signal_date <= as_of:
                        latest_signal = {
                            "signal_date": signal_date.isoformat(),
                            "signal_high": float(row["high"]),
                            "signal_low": float(row["low"]),
                            "rally_start": first_idx.date().isoformat(),
                            "rally_high_date": window.index[max(highest_day - 1, 0)].date().isoformat(),
                            "rally_pct": round(rally_pct, 4),
                            "velocity": round(velocity, 4),
                            "reaction_volume_ratio": round(reaction_volume / prior20_volume, 4),
                            "filing_date": filed_date.isoformat(),
                            "entry_window_days": int(self.pipeline.cfg["technical"]["entry_window_days"]),
                            "setup": "SETUP_2",
                        }
                    break

        if latest_signal is None:
            return ModuleBResult(instrument_id, as_of, True, (), None)
        return ModuleBResult(instrument_id, as_of, True, (), latest_signal)
