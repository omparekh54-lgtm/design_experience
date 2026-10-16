from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text


class PointInTimeQueries:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _split_instrument_id(instrument_id: str) -> tuple[str, str]:
        if ":" not in instrument_id:
            raise ValueError(f"unsupported instrument id: {instrument_id}")
        exchange, symbol = instrument_id.split(":", 1)
        trading_symbol = f"{symbol}-EQ" if exchange == "NSE" else symbol
        return exchange, trading_symbol

    def _db_instrument_id(self, conn, instrument_id: str) -> int:
        exchange, trading_symbol = self._split_instrument_id(instrument_id)
        value = conn.execute(
            text(
                """
                select id from instruments
                where exchange=:exchange and trading_symbol=:trading_symbol
                  and active=true
                order by valid_from desc
                limit 1
                """
            ),
            {"exchange": exchange, "trading_symbol": trading_symbol},
        ).scalar_one_or_none()
        if value is None:
            raise KeyError(f"instrument not found: {instrument_id}")
        return int(value)

    def latest_financials_as_of(
        self,
        instrument_id: str,
        as_of: datetime,
        prefer_consolidated: bool = True,
    ) -> dict[str, Any] | None:
        """Return only a filing that was disseminated by `as_of`.

        If both consolidated and standalone are available at the same latest
        point, consolidated is preferred for the PMS v1 rule set.
        """
        with self.engine.connect() as conn:
            db_id = self._db_instrument_id(conn, instrument_id)
            filing = conn.execute(
                text(
                    """
                    select id, period_end, filing_timestamp, source,
                           consolidated, revision_number
                    from financial_filings
                    where instrument_id=:instrument_id
                      and filing_timestamp <= :as_of
                    order by filing_timestamp desc,
                             case when consolidated=:prefer_consolidated then 0 else 1 end,
                             revision_number desc
                    limit 1
                    """
                ),
                {
                    "instrument_id": db_id,
                    "as_of": as_of,
                    "prefer_consolidated": prefer_consolidated,
                },
            ).mappings().first()
            if filing is None:
                return None

            facts = conn.execute(
                text("select fact_name, value, unit from financial_facts where filing_id=:filing_id"),
                {"filing_id": filing["id"]},
            ).mappings().all()
            return {
                "instrument_id": instrument_id,
                "period_end": filing["period_end"],
                "filed_at": filing["filing_timestamp"],
                "source": filing["source"],
                "consolidated": filing["consolidated"],
                "revision_number": filing["revision_number"],
                "facts": {row["fact_name"]: row["value"] for row in facts},
                "units": {row["fact_name"]: row["unit"] for row in facts if row["unit"]},
            }

    def daily_prices(
        self,
        instrument_id: str,
        start: date,
        end: date,
        preferred_source: str | None = "angel_one",
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            db_id = self._db_instrument_id(conn, instrument_id)
            params = {"instrument_id": db_id, "start": start, "end": end}
            source_clause = ""
            if preferred_source:
                source_clause = "and source=:source"
                params["source"] = preferred_source
            rows = conn.execute(
                text(
                    f"""
                    select trade_date, open, high, low, close, volume, turnover, source
                    from daily_prices
                    where instrument_id=:instrument_id
                      and trade_date between :start and :end
                      {source_clause}
                    order by trade_date
                    """
                ),
                params,
            ).mappings().all()
            return [dict(row) for row in rows]

    def surveillance_as_of(
        self,
        instrument_id: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        with self.engine.connect() as conn:
            db_id = self._db_instrument_id(conn, instrument_id)
            rows = conn.execute(
                text(
                    """
                    select surveillance_type, stage, source, effective_from, effective_to
                    from surveillance_history
                    where instrument_id=:instrument_id
                      and effective_from <= :as_of
                      and (effective_to is null or effective_to > :as_of)
                    """
                ),
                {"instrument_id": db_id, "as_of": as_of},
            ).mappings().all()
            types = {str(row["surveillance_type"]).upper() for row in rows}
            return {
                "instrument_id": instrument_id,
                "as_of": as_of,
                "asm": "ASM" in types,
                "gsm": "GSM" in types,
                "rows": [dict(row) for row in rows],
            }
