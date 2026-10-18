from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import text

from packages.data_layer.features import compute_features
from packages.pms.backtest import EventDrivenBacktester, MarketBar, Signal
from packages.pms.module_b_pipeline import ModuleBPipeline
from packages.pms.module_b_setup2 import EarningsAnchoredSetup2
from packages.pms.research import ResearchVariant
from packages.pms.module_a_context import IST


class DatabaseBacktestStrategy:
    """Research adapter. C rankings are historical selected lists, not portfolio replay."""

    def __init__(
        self,
        engine,
        symbols: list[str],
        variant: ResearchVariant | None = None,
        cfg: dict | None = None,
    ):
        self.engine = engine
        self.symbols = symbols
        self.module_b = ModuleBPipeline(engine, cfg)
        self.setup2 = EarningsAnchoredSetup2(self.module_b)
        self.variant = variant or ResearchVariant("DEFAULT")
        self.cfg = cfg
        self._bars: list[MarketBar] = []

    def build_bars(self, start: date, end: date) -> list[MarketBar]:
        bars: list[MarketBar] = []
        for instrument_id in self.symbols:
            candles = self.module_b._candles(
                instrument_id, end, lookback_days=max(500, (end - start).days + 300)
            )
            if not candles:
                continue
            frame = compute_features(candles).data.dropna(subset=["ema9", "ema21"])
            for i in range(1, len(frame)):
                d = frame.index[i].date()
                if d < start or d > end:
                    continue
                row = frame.iloc[i]
                prev = frame.iloc[i - 1]
                bars.append(
                    MarketBar(
                        instrument_id,
                        d,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["ema9"]),
                        float(prev["ema9"]),
                        float(prev["ema21"]),
                    )
                )
        self._bars = sorted(bars, key=lambda x: (x.trade_date, x.instrument_id))
        return self._bars

    def _module_c_selected(self, instrument_id: str, d: date) -> bool:
        if not self.variant.use_module_c:
            return True
        exchange, symbol = instrument_id.split(":", 1)
        trading_symbol = f"{symbol}-EQ" if exchange == "NSE" else symbol
        with self.engine.connect() as conn:
            value = conn.execute(
                text("""
                select r.selected from module_c_rankings r
                join instruments i on i.id=r.instrument_id
                where r.trade_date=:d and i.exchange=:e and i.trading_symbol=:s
                order by r.id desc limit 1
            """),
                {"d": d, "e": exchange, "s": trading_symbol},
            ).scalar_one_or_none()
        return bool(value)

    def signals_for_close(self, d: date) -> list[Signal]:
        signals: list[Signal] = []
        for instrument_id in self.symbols:
            if self.variant.setup_2:
                result = self.setup2.evaluate(
                    instrument_id, d, require_module_a=self.variant.use_module_a
                )
            else:
                result = self.module_b.evaluate(
                    instrument_id, d, require_module_a=self.variant.use_module_a
                )
            if (
                not result.eligible_universe
                or not result.signal
                or not self._module_c_selected(instrument_id, d)
            ):
                continue
            signal_date = date.fromisoformat(str(result.signal["signal_date"]))
            if signal_date == d:
                signals.append(
                    Signal(
                        instrument_id,
                        d,
                        float(result.signal["signal_high"]),
                        float(result.signal["signal_low"]),
                    )
                )
        return signals

    def capacity_for_date(self, d: date) -> int:
        if not self.variant.use_module_e:
            return 7
        with self.engine.connect() as conn:
            value = conn.execute(
                text(
                    "select max_open_trades from market_regime_history where trade_date<:d order by trade_date desc limit 1"
                ),
                {"d": d},
            ).scalar_one_or_none()
        return int(value) if value is not None else 0

    def eligibility_for_reentry(self, instrument_id: str, d: date) -> bool:
        if not self.variant.use_module_a:
            return True
        try:
            _, decision = self.module_b.module_a.evaluate(
                instrument_id, datetime.combine(d, time(9, 15), tzinfo=IST)
            )
            return bool(decision.eligible)
        except Exception:
            return False

    def eligibility_for_signal(self, instrument_id: str, d: date) -> bool:
        if not self.variant.use_module_a:
            return True
        try:
            _, decision = self.module_b.module_a.evaluate(
                instrument_id, self.module_b._stamp(d)
            )
            return bool(decision.eligible)
        except Exception:
            return False

    def capacity_for_close(self, d: date) -> int:
        if not self.variant.use_module_e:
            return 7
        with self.engine.connect() as conn:
            value = conn.execute(
                text(
                    "select max_open_trades from market_regime_history where trade_date<=:d order by trade_date desc limit 1"
                ),
                {"d": d},
            ).scalar_one_or_none()
        return int(value) if value is not None else 0

    def run(self, start: date, end: date, initial_capital: float | None = None):
        bars = self.build_bars(start, end)
        engine = EventDrivenBacktester(initial_capital=initial_capital, cfg=self.cfg)
        return engine.run(
            bars,
            self.signals_for_close,
            eligibility_provider=self.eligibility_for_reentry,
            capacity_provider=self.capacity_for_date,
            allow_reentry=self.variant.reentry,
            trading_sessions=sorted(self.module_b._calendar(end).sessions),
            signal_eligibility_provider=self.eligibility_for_signal,
            close_capacity_provider=self.capacity_for_close,
        )
