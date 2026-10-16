from __future__ import annotations

import argparse
import os
from datetime import date

from sqlalchemy import create_engine, text

from packages.data_layer.angel_auth import create_angel_session
from packages.data_layer.angel_one import AngelOneMarketDataProvider
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, filing_from_exchange_record
from packages.data_layer.ingestion import DataIngestionService
from packages.data_layer.instrument_master import AngelInstrumentMasterLoader
from packages.data_layer.postgres_storage import PostgresStorage
from packages.pms.module_e_pipeline import ModuleEPipeline
from packages.pms.research_dataset import export_research_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill PMS historical research dataset")
    parser.add_argument("--symbols", required=True, help="Comma-separated NSE symbols")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", default="research_data")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-regime", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    symbols = sorted({x.strip().upper() for x in args.symbols.split(",") if x.strip()})
    storage = PostgresStorage(database_url=database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    if not args.skip_prices:
        master = AngelInstrumentMasterLoader().download()
        storage.upsert_instruments(master.instruments)
        selected = [x for x in master.instruments if x.exchange == "NSE" and x.symbol.upper() in symbols]
        missing = set(symbols) - {x.symbol.upper() for x in selected}
        if missing:
            raise RuntimeError(f"Missing Angel instruments: {sorted(missing)}")
        session, _ = create_angel_session()
        result = DataIngestionService(storage).ingest_daily_prices(AngelOneMarketDataProvider(session), selected, args.start, args.end)
        print(f"prices attempted={result.attempted} written={result.written} issues={len(result.issues)}")

    if not args.skip_financials:
        provider = NSEIntegratedFinancialsProvider()
        for symbol in symbols:
            records = provider.list_filings(symbol=symbol, from_date=args.start, to_date=args.end, page=1, size=500)
            filings = []
            for record in records:
                xbrl = None
                if record.xbrl_uri:
                    try:
                        xbrl = provider.fetch_xbrl(record)
                    except Exception as exc:
                        print(f"WARN XBRL {symbol} {record.period_end}: {exc}")
                filings.append(filing_from_exchange_record(record, xbrl))
            if filings:
                storage.append_filings(filings)
            print(f"financials {symbol}: {len(filings)}")

    if not args.skip_regime:
        dates = []
        with engine.connect() as conn:
            dates = list(conn.execute(text("select distinct trade_date from daily_prices where trade_date between :s and :e order by trade_date"), {"s": args.start, "e": args.end}).scalars())
        regime = ModuleEPipeline(engine)
        for d in dates:
            try:
                regime.evaluate(d, persist=True)
            except Exception as exc:
                # Regime requires NIFTY MIDCAP 400, India VIX and broad-universe history.
                print(f"WARN regime {d}: {exc}")

    manifest = export_research_dataset(engine, args.out, args.start, args.end)
    print(f"dataset_manifest={manifest.manifest_id} root={manifest.root_path}")
    print("NOTE: load historical shareholding/pledge, ASM/GSM, index constituents and corporate actions with ingest_reference_csv.py before production research runs.")


if __name__ == "__main__":
    main()
