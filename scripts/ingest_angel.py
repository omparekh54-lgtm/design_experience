from __future__ import annotations

import argparse
import os
from datetime import date

from packages.data_layer.angel_auth import create_angel_session
from packages.data_layer.angel_one import AngelOneMarketDataProvider
from packages.data_layer.ingestion import DataIngestionService
from packages.data_layer.instrument_master import AngelInstrumentMasterLoader
from packages.data_layer.postgres_storage import PostgresStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Angel One cash-market data into the PMS database")
    parser.add_argument("--symbols", required=True, help="Comma-separated NSE symbols, e.g. RELIANCE,TCS")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--skip-master-refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    storage = PostgresStorage(database_url=database_url)
    ingestion = DataIngestionService(storage)
    loader = AngelInstrumentMasterLoader()

    # Load the current token map. Persist it unless explicitly skipped.
    snapshot = loader.download()
    if not args.skip_master_refresh:
        storage.upsert_instruments(snapshot.instruments)

    requested = {symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()}
    selected = [
        instrument
        for instrument in snapshot.instruments
        if instrument.exchange == "NSE" and instrument.symbol.upper() in requested
    ]
    found = {instrument.symbol.upper() for instrument in selected}
    missing = requested - found
    if missing:
        raise RuntimeError(f"Symbols not found in Angel instrument master: {', '.join(sorted(missing))}")

    session, _tokens = create_angel_session()
    provider = AngelOneMarketDataProvider(session)
    result = ingestion.ingest_daily_prices(provider, selected, args.start, args.end)

    print(f"dataset={result.dataset} attempted={result.attempted} written={result.written} issues={len(result.issues)}")
    for issue in result.issues:
        print(f"{issue.severity} {issue.instrument_id} {issue.code}: {issue.message}")


if __name__ == "__main__":
    main()
