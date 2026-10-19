"""Synthetic input tape through shared C/E and the actual lifecycle/fee engine."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from packages.data_layer.models import Candle
from packages.data_layer.queries import PointInTimeQueries
from packages.pms.backtest_matrix import Case, Period, metrics
from packages.pms.config import load_strategy_config
from packages.pms.market_calendar import MarketCalendar
from packages.pms.matrix_replay import (
    HistoricalA,
    MatrixReplay,
    ResearchB,
    ResearchC,
    ResearchE,
)
from packages.pms.module_a_context import IST
from packages.pms.module_b_pipeline import ModuleBResult
from packages.pms.research import ResearchVariant


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows


class TapeEngine:
    def __init__(self):
        self.calls = []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def begin(self):
        pytest.fail("Historical replay must never persist strategy/account state")

    def execute(self, query, params=None):
        sql = str(query)
        self.calls.append(sql)
        assert "listed_from,delisted_on from instruments" in sql, sql
        return Rows(
            [
                dict(
                    instrument_id="NSE:TEST",
                    listed_from=date(2010, 1, 1),
                    delisted_on=None,
                )
            ]
        )


def test_isolated_portfolio_recomputes_c_and_e_without_saved_production_decisions(
    monkeypatch,
):
    start = date(2020, 2, 3)
    sessions = list(
        pd.bdate_range(end=start - timedelta(days=1), periods=270).date
    ) + list(pd.bdate_range(start=start, periods=15).date)
    calendar = MarketCalendar(sessions)
    monkeypatch.setattr(
        MarketCalendar, "from_database", classmethod(lambda cls, *a: calendar)
    )
    monkeypatch.setattr(MatrixReplay, "_actions", lambda self: ())
    engine = TapeEngine()
    cfg = deepcopy(load_strategy_config())
    cfg["backtest"]["initial_capital"] = 1000000
    arow = dict(
        instrument_id="NSE:TEST",
        snapshot=dict(sector="IT", reference_context={"id": 1}),
        decision=dict(eligible=True, score=90, triggers=[], failed_rules=[]),
    )
    monkeypatch.setattr(
        HistoricalA,
        "scan",
        lambda self, *a, **kw: dict(
            eligible=1, results=[arow], configuration_ready=True
        ),
    )
    monkeypatch.setattr(
        HistoricalA, "evaluate", lambda *a, **kw: (None, SimpleNamespace(eligible=True))
    )
    requested = []
    tape = []
    for i, day in enumerate(sessions):
        close = 100 + i * 0.05
        high, low, open_ = close + 3, close - 3, close
        if day == start:
            open_, high, low, close = 113, 115, 110, 113
        elif day == sessions[271]:
            open_, high, low, close = 116, 118, 114, 117
        elif day == sessions[272]:
            open_, high, low, close = 118, 121, 116, 119
        elif day == sessions[273]:
            open_, high, low, close = 118, 120, 90, 100
        tape.append(
            Candle(
                "NSE:TEST",
                datetime.combine(day, time(15, 30), tzinfo=IST),
                Decimal(str(open_)),
                Decimal(str(high)),
                Decimal(str(low)),
                Decimal(str(close)),
                1000000,
                "NSE",
                turnover=Decimal(100000000),
            )
        )

    def candles(self, instrument, day, lookback_days=500):
        requested.append(day)
        return [c for c in tape if c.timestamp.date() <= day]

    monkeypatch.setattr(ResearchB, "_candles", candles)
    signal = dict(
        setup="SETUP_1",
        status="SIGNAL_CREATED",
        signal_date=start.isoformat(),
        signal_high=115,
        signal_low=110,
        velocity=3,
        volume_ratio=4,
    )
    monkeypatch.setattr(
        ResearchB,
        "evaluate",
        lambda self, instrument, day, **kw: ModuleBResult(
            instrument, day, True, (), signal if day == start else None
        ),
    )
    monkeypatch.setattr(ResearchC, "_benchmark_returns", lambda *a: {63: 0, 126: 0})
    monkeypatch.setattr(
        ResearchE,
        "_benchmarks",
        lambda *a: dict(
            midcap_close=150,
            midcap_dma50=140,
            midcap_dma200=130,
            midcap_dma200_slope_pct=2,
            india_vix=12,
            india_vix_5d_change_pct=-5,
        ),
    )
    monkeypatch.setattr(
        ResearchE,
        "_coverage",
        lambda *a: (
            [{"instrument_id": "NSE:TEST", "id": 1}],
            dict(master_universe=1, liquid_universe=1, unclassified=0),
        ),
    )
    monkeypatch.setattr(
        ResearchE,
        "_breadth",
        lambda *a: (
            dict(
                breadth_above_50dma_pct=80,
                breadth_above_21ema_pct=80,
                advance_decline_10d=2,
                new_highs_pct=10,
                new_lows_pct=1,
                net_highs_trend_pp=1,
                valid_rallies_pct=50,
            ),
            {},
        ),
    )
    case = Case("FULL", ResearchVariant("FULL"))
    replay = MatrixReplay(engine, ["NSE:TEST"], cfg, case, start, sessions[275])
    monkeypatch.setattr(
        replay.b.reference, "earnings_within_trading_window", lambda *a, **kw: False
    )
    result, diag = replay.run()
    assert (
        len(result.fills) == 2
        and result.fills[0].side == "BUY"
        and result.fills[1].side == "SELL"
    )
    assert result.fills[0].trade_date == sessions[271] and result.fills[0].price == 116
    assert result.closed_trades == 1
    assert (
        metrics(result, Period("FIXTURE", start, sessions[275]))["closed_trades"] == 1
    )
    assert all(d <= sessions[275] for d in requested)
    assert len(diag["regime"]) == 6 and all(
        0 <= r["capacity"] <= 7 for r in diag["regime"]
    )
    assert sum(r["selected"] for r in diag["allocation"]) == 1
    assert (
        len(engine.calls) == 1
    )  # Listing metadata only; no stored C/E/safety/PAPER reads.
    assert result.total_costs > 0 and result.ending_cash == pytest.approx(
        result.ending_equity
    )


def test_historical_query_mode_includes_inactive_securities_and_rejects_ambiguous_identity():
    class Engine:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def connect(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, q, p):
            self.calls.append((str(q), p))
            return Rows(self.rows)

    engine = Engine([7])
    query = PointInTimeQueries(engine, historical=True)
    assert query._db_instrument_id(engine, "NSE:OLD") == 7
    assert "active" not in engine.calls[0][0]
    engine.rows = []
    query.financial_histories_as_of(datetime(2020, 1, 2, 18, tzinfo=IST))
    assert engine.calls[-1][1]["historical"] is True
    engine.rows = [7, 8]
    with pytest.raises(ValueError, match="ambiguous"):
        query._db_instrument_id(engine, "NSE:OLD")
