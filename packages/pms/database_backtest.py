from __future__ import annotations

from datetime import date

from packages.data_layer.features import compute_features
from packages.pms.backtest import EventDrivenBacktester, MarketBar, Signal
from packages.pms.module_b_pipeline import ModuleBPipeline


class DatabaseBacktestStrategy:
    """Adapter that reuses the production Module A/B data pipelines in backtests."""

    def __init__(self, engine, symbols: list[str]):
        self.engine = engine
        self.symbols = symbols
        self.module_b = ModuleBPipeline(engine)
        self._bars: list[MarketBar] = []

    def build_bars(self, start: date, end: date) -> list[MarketBar]:
        bars: list[MarketBar] = []
        for instrument_id in self.symbols:
            candles = self.module_b._candles(instrument_id, end, lookback_days=max(500, (end - start).days + 300))
            if not candles:
                continue
            frame = compute_features(candles).data.dropna(subset=["ema9", "ema21"])
            for i in range(1, len(frame)):
                d = frame.index[i].date()
                if d < start or d > end:
                    continue
                row = frame.iloc[i]
                prev = frame.iloc[i - 1]
                bars.append(MarketBar(
                    instrument_id=instrument_id,
                    trade_date=d,
                    open=float(row["open"]), high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
                    ema9=float(row["ema9"]), previous_ema9=float(prev["ema9"]), previous_ema21=float(prev["ema21"]),
                ))
        self._bars = sorted(bars, key=lambda x: (x.trade_date, x.instrument_id))
        return self._bars

    def signals_for_close(self, d: date) -> list[Signal]:
        signals: list[Signal] = []
        for instrument_id in self.symbols:
            result = self.module_b.evaluate(instrument_id, d)
            if not result.eligible_universe or not result.signal:
                continue
            signal_date = date.fromisoformat(str(result.signal["signal_date"]))
            if signal_date != d:
                continue
            signals.append(Signal(
                instrument_id=instrument_id,
                signal_date=d,
                signal_high=float(result.signal["signal_high"]),
                signal_low=float(result.signal["signal_low"]),
            ))
        return signals

    def run(self, start: date, end: date, initial_capital: float | None = None):
        bars = self.build_bars(start, end)
        engine = EventDrivenBacktester(initial_capital=initial_capital)
        return engine.run(bars, self.signals_for_close)
