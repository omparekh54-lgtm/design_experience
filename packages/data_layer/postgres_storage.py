from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy import Engine, create_engine, text

from .models import Candle, FinancialFiling, Instrument, SurveillanceState


class PostgresStorage:
    def __init__(self, database_url: str | None = None, engine: Engine | None = None):
        if engine is None and not database_url:
            raise ValueError("database_url or engine is required")
        self.engine = engine or create_engine(database_url, pool_pre_ping=True)

    def _instrument_id(self, conn, instrument: Instrument) -> int:
        row = conn.execute(
            text(
                """
                insert into instruments (
                    isin, nse_symbol, angel_token, sector, active,
                    exchange, trading_symbol, name, metadata, updated_at
                ) values (
                    :isin, :nse_symbol, :angel_token, :sector, :active,
                    :exchange, :trading_symbol, :name, cast(:metadata as jsonb), now()
                )
                on conflict (exchange, trading_symbol)
                where exchange is not null and trading_symbol is not null
                do update set
                    isin = coalesce(excluded.isin, instruments.isin),
                    nse_symbol = coalesce(excluded.nse_symbol, instruments.nse_symbol),
                    angel_token = coalesce(excluded.angel_token, instruments.angel_token),
                    sector = coalesce(excluded.sector, instruments.sector),
                    active = excluded.active,
                    name = coalesce(excluded.name, instruments.name),
                    metadata = instruments.metadata || excluded.metadata,
                    updated_at = now()
                returning id
                """
            ),
            {
                "isin": instrument.isin,
                "nse_symbol": instrument.symbol if instrument.exchange == "NSE" else None,
                "angel_token": instrument.token,
                "sector": instrument.sector,
                "active": instrument.active,
                "exchange": instrument.exchange,
                "trading_symbol": instrument.trading_symbol,
                "name": instrument.name,
                "metadata": json.dumps(instrument.metadata),
            },
        ).first()
        if row is None:
            raise RuntimeError(f"failed to resolve instrument {instrument.instrument_id}")
        return int(row[0])

    def upsert_instruments(self, instruments: Iterable[Instrument]) -> int:
        rows = list(instruments)
        with self.engine.begin() as conn:
            for instrument in rows:
                self._instrument_id(conn, instrument)
        return len(rows)

    def _lookup_id(self, conn, instrument_id: str) -> int:
        if ":" not in instrument_id:
            raise ValueError(f"unsupported instrument id: {instrument_id}")
        exchange, symbol = instrument_id.split(":", 1)
        trading_symbol = f"{symbol}-EQ" if exchange == "NSE" else symbol
        row = conn.execute(
            text("select id from instruments where exchange=:exchange and trading_symbol=:trading_symbol"),
            {"exchange": exchange, "trading_symbol": trading_symbol},
        ).first()
        if row is None:
            raise KeyError(f"instrument not found: {instrument_id}")
        return int(row[0])

    def upsert_candles(self, candles: Iterable[Candle]) -> int:
        rows = list(candles)
        with self.engine.begin() as conn:
            cache: dict[str, int] = {}
            for candle in rows:
                db_id = cache.get(candle.instrument_id)
                if db_id is None:
                    db_id = self._lookup_id(conn, candle.instrument_id)
                    cache[candle.instrument_id] = db_id
                conn.execute(
                    text(
                        """
                        insert into daily_prices (
                            instrument_id, trade_date, open, high, low, close, volume, source
                        ) values (:instrument_id, :trade_date, :open, :high, :low, :close, :volume, :source)
                        on conflict (instrument_id, trade_date, source)
                        do update set
                            open=excluded.open, high=excluded.high, low=excluded.low,
                            close=excluded.close, volume=excluded.volume, ingested_at=now()
                        """
                    ),
                    {
                        "instrument_id": db_id,
                        "trade_date": candle.timestamp.date(),
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "source": candle.source,
                    },
                )
        return len(rows)

    def append_filings(self, filings: Iterable[FinancialFiling]) -> int:
        rows = list(filings)
        with self.engine.begin() as conn:
            cache: dict[str, int] = {}
            for filing in rows:
                db_id = cache.get(filing.instrument_id)
                if db_id is None:
                    db_id = self._lookup_id(conn, filing.instrument_id)
                    cache[filing.instrument_id] = db_id
                filing_id = conn.execute(
                    text(
                        """
                        insert into financial_filings (
                            instrument_id, period_end, filing_timestamp, source,
                            consolidated, revision_number
                        ) values (:instrument_id, :period_end, :filing_timestamp, :source, :consolidated, :revision_number)
                        on conflict (
                            instrument_id, period_end, filing_timestamp, source, revision_number
                        ) do update set
                            consolidated=excluded.consolidated,
                            ingested_at=now()
                        returning id
                        """
                    ),
                    {
                        "instrument_id": db_id,
                        "period_end": filing.period_end,
                        "filing_timestamp": filing.filed_at,
                        "source": filing.source,
                        "consolidated": filing.statement_scope.lower() == "consolidated",
                        "revision_number": filing.version,
                    },
                ).scalar_one()
                facts = {
                    "sales": filing.sales,
                    "operating_profit": filing.operating_profit,
                    "pat": filing.pat,
                    "diluted_eps": filing.diluted_eps,
                    "pbt": filing.pbt,
                    "other_income": filing.other_income,
                    "exceptional_items": filing.exceptional_items,
                    "operating_cash_flow": filing.operating_cash_flow,
                    "debt": filing.debt,
                    "equity": filing.equity,
                    "interest_expense": filing.interest_expense,
                }
                for fact_name, value in facts.items():
                    if value is not None:
                        conn.execute(
                            text(
                                """
                                insert into financial_facts (filing_id, fact_name, value)
                                values (:filing_id, :fact_name, :value)
                                on conflict (filing_id, fact_name)
                                do update set value=excluded.value
                                """
                            ),
                            {"filing_id": filing_id, "fact_name": fact_name, "value": value},
                        )
        return len(rows)

    def record_surveillance(self, states: Iterable[SurveillanceState]) -> int:
        rows = list(states)
        with self.engine.begin() as conn:
            for state in rows:
                db_id = self._lookup_id(conn, state.instrument_id)
                for surveillance_type, enabled in (("ASM", state.asm), ("GSM", state.gsm)):
                    if enabled:
                        conn.execute(
                            text(
                                """
                                insert into surveillance_history (
                                    instrument_id, surveillance_type, stage, effective_from, source
                                ) values (:instrument_id, :surveillance_type, :stage, :effective_from, :source)
                                """
                            ),
                            {
                                "instrument_id": db_id,
                                "surveillance_type": surveillance_type,
                                "stage": state.stage,
                                "effective_from": state.observed_at,
                                "source": state.source,
                            },
                        )
        return len(rows)
