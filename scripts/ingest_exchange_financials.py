from __future__ import annotations

import argparse
import os
from datetime import date, datetime

from packages.data_layer.exchange_financials import (
    BSEFinancialResultsProvider,
    ExchangeFinancialsError,
    NSEIntegratedFinancialsProvider,
    filing_from_exchange_record,
)
from packages.data_layer.models import Instrument
from packages.data_layer.postgres_storage import PostgresStorage


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _nse_instrument(symbol: str) -> Instrument:
    symbol = symbol.upper()
    return Instrument(
        instrument_id=f"NSE:{symbol}",
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        metadata={"fundamentals_source": "NSE"},
    )


def _bse_instrument(code: str, symbol: str) -> Instrument:
    symbol = symbol.upper()
    return Instrument(
        instrument_id=f"BSE:{symbol}",
        exchange="BSE",
        symbol=symbol,
        trading_symbol=symbol,
        metadata={"bse_code": str(code), "fundamentals_source": "BSE"},
    )


def ingest_nse(
    storage: PostgresStorage,
    symbols: list[str],
    from_date: date | None,
    to_date: date | None,
    strict_xbrl: bool,
) -> tuple[int, int]:
    provider = NSEIntegratedFinancialsProvider()
    written = 0
    xbrl_failures = 0
    for symbol in symbols:
        storage.upsert_instruments([_nse_instrument(symbol)])
        records = provider.list_filings(
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            page=1,
            size=200,
        )
        filings = []
        for record in records:
            xbrl = None
            if record.xbrl_uri:
                try:
                    xbrl = provider.fetch_xbrl(record)
                except Exception as exc:  # network/exchange files can be transient
                    xbrl_failures += 1
                    if strict_xbrl:
                        raise
                    print(f"WARN NSE {symbol} {record.period_end}: XBRL fetch failed: {exc}")
            filings.append(filing_from_exchange_record(record, xbrl))
        if filings:
            written += storage.append_filings(filings)
        print(f"NSE {symbol}: discovered={len(records)} stored={len(filings)}")
    return written, xbrl_failures


def ingest_bse(
    storage: PostgresStorage,
    mappings: list[tuple[str, str]],
    strict_xbrl: bool,
) -> tuple[int, int]:
    provider = BSEFinancialResultsProvider()
    written = 0
    xbrl_failures = 0
    for code, symbol in mappings:
        storage.upsert_instruments([_bse_instrument(code, symbol)])
        records = provider.list_filings(code, symbol=symbol)
        filings = []
        for record in records:
            xbrl = None
            if record.xbrl_uri:
                try:
                    xbrl = provider.fetch_xbrl(record)
                except Exception as exc:
                    xbrl_failures += 1
                    if strict_xbrl:
                        raise
                    print(f"WARN BSE {symbol} {record.period_end}: XBRL fetch failed: {exc}")
            filings.append(filing_from_exchange_record(record, xbrl))
        if filings:
            written += storage.append_filings(filings)
        print(f"BSE {symbol} ({code}): discovered={len(records)} stored={len(filings)}")
    return written, xbrl_failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official NSE/BSE financial filings into PMS PostgreSQL")
    parser.add_argument("--nse-symbols", default="", help="Comma-separated NSE symbols")
    parser.add_argument(
        "--bse",
        action="append",
        default=[],
        metavar="CODE:SYMBOL",
        help="BSE scrip code and canonical symbol; repeat flag for multiple companies",
    )
    parser.add_argument("--from-date", help="NSE filing discovery start date YYYY-MM-DD")
    parser.add_argument("--to-date", help="NSE filing discovery end date YYYY-MM-DD")
    parser.add_argument("--strict-xbrl", action="store_true", help="Fail the run if any XBRL cannot be downloaded/parsed")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    storage = PostgresStorage(database_url=database_url)

    symbols = [s.strip().upper() for s in args.nse_symbols.split(",") if s.strip()]
    bse_mappings: list[tuple[str, str]] = []
    for value in args.bse:
        if ":" not in value:
            raise SystemExit(f"Invalid --bse value {value!r}; expected CODE:SYMBOL")
        code, symbol = value.split(":", 1)
        bse_mappings.append((code.strip(), symbol.strip().upper()))

    started = datetime.now()
    nse_written = bse_written = nse_failures = bse_failures = 0
    if symbols:
        nse_written, nse_failures = ingest_nse(
            storage,
            symbols,
            _date(args.from_date),
            _date(args.to_date),
            args.strict_xbrl,
        )
    if bse_mappings:
        bse_written, bse_failures = ingest_bse(storage, bse_mappings, args.strict_xbrl)

    print(
        "done",
        {
            "seconds": round((datetime.now() - started).total_seconds(), 2),
            "nse_filings": nse_written,
            "bse_filings": bse_written,
            "xbrl_failures": nse_failures + bse_failures,
        },
    )


if __name__ == "__main__":
    main()
