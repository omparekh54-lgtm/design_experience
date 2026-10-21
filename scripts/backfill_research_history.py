from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import date
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine

from packages.data_layer.angel_one import AngelOneMarketDataProvider
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider
from packages.data_layer.ingestion import DataIngestionService
from packages.data_layer.instrument_master import AngelInstrumentMasterLoader
from packages.data_layer.postgres_storage import PostgresStorage
from packages.pms.config import load_strategy_config
from packages.pms.data_integrity import AuditScope, IntegrityAuditor, write_report
from packages.pms.historical_dataset import discover_financial_history, v2_financial_parser, write_completion_plan, require_financial_period_identity
from packages.pms.matrix_data import consistent_inputs, code_fingerprint
from packages.pms.research_dataset import export_research_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill PMS historical research dataset")
    parser.add_argument("--symbols", required=True, help="Comma-separated NSE symbols")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", default="research_data")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-regime", action="store_true", help="Deprecated compatibility flag; regime is always recomputed inside isolated replay")
    parser.add_argument("--max-financial-pages", type=int, default=100)
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    parser_v2 = v2_financial_parser() if not args.skip_financials else None
    if args.end < args.start or args.end >= date.today():
        parser.error("Ordered completed historical dates required")
    symbols = sorted({x.strip().upper() for x in args.symbols.split(",") if x.strip()})
    if not symbols:
        parser.error("At least one NSE symbol is required")
    storage = PostgresStorage(database_url=database_url)
    engine = create_engine(database_url, pool_pre_ping=True,
                           connect_args={"options": "-c search_path=pms,public"})
    if parser_v2 is not None:
        require_financial_period_identity(engine)

    if not args.skip_prices:
        from packages.data_layer.angel_auth import create_angel_session

        master = AngelInstrumentMasterLoader().download()
        selected = [x for x in master.instruments if x.exchange == "NSE" and x.symbol.upper() in symbols]
        missing = set(symbols) - {x.symbol.upper() for x in selected}
        if missing:
            raise RuntimeError(f"Missing Angel instruments: {sorted(missing)}")
        storage.upsert_instruments(selected)
        session, _ = create_angel_session()
        result = DataIngestionService(storage).ingest_daily_prices(AngelOneMarketDataProvider(session), selected, args.start, args.end)
        print(f"prices attempted={result.attempted} written={result.written} issues={len(result.issues)}")

    if not args.skip_financials:
        provider = NSEIntegratedFinancialsProvider()
        for symbol in symbols:
            records = discover_financial_history(provider, symbol, args.start, args.end,
                max_pages=args.max_financial_pages)
            filings = []
            for record in records:
                xbrl = None
                if record.xbrl_uri:
                    xbrl = provider.fetch_xbrl(record)
                else:
                    raise RuntimeError("Financial filing lacks an original XBRL source; review required")
                filings.extend(parser_v2(record, xbrl))
            if filings:
                storage.append_filings(filings)
            print(f"financials {symbol}: {len(filings)}")

    # Regime decisions belong to each isolated replay, never to source backfill.
    cfg = load_strategy_config()
    audit_path = Path(args.out) / ("audit-" + uuid4().hex + ".json")
    with consistent_inputs(engine) as snapshot:
        report = IntegrityAuditor(snapshot, AuditScope(args.start, args.end), cfg,
            symbols=["NSE:" + s for s in symbols], code_version=code_fingerprint()).run()
    write_report(report, audit_path)
    plan = write_completion_plan(report, audit_path.with_suffix(".repair.json"))
    print(f"audit={report['summary']['status']} completion={plan['status']} report={audit_path}")

    manifest = export_research_dataset(engine, args.out, args.start, args.end)
    print(f"dataset_manifest={manifest.manifest_id} root={manifest.root_path}")
    print("Export is not evidence of dataset completeness; resolve the audit repair plan before acceptance.")
    engine.dispose()
    return 0 if plan["dataset_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
