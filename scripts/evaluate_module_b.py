from __future__ import annotations
import argparse, json, os, sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine
from packages.pms.module_a_context import IST
from packages.pms.module_b_pipeline import ModuleBPipeline
from packages.pms.module_b_setup2 import EarningsAnchoredSetup2
from packages.pms.market_calendar import refresh_holiday_calendar


def main():
    parser = argparse.ArgumentParser(
        description="Module B technical scanner; Setup 1 is production, Setup 2 is research-only."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--instrument", help="For example NSE:RELIANCE")
    target.add_argument("--all", action="store_true")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=datetime.now(IST).date()
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--details", action="store_true")
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Bypass Module A for single-stock technical diagnostics only",
    )
    parser.add_argument("--setup", choices=["1", "2"], default="1")
    parser.add_argument(
        "--refresh-calendar",
        action="store_true",
        help="Fetch and save the current official NSE holiday year",
    )
    args = parser.parse_args()
    if args.as_of > datetime.now(IST).date():
        parser.error("--as-of must not be in the future")
    if args.research_only and (args.persist or args.all):
        parser.error("research-only requires a single stock and cannot persist")
    if args.setup == "2" and (args.persist or args.all):
        parser.error("Setup 2 is a single-stock research/backtest variant")
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("PGOPTIONS", "-c search_path=pms,public")
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL/PMS_DATABASE_URL is required in .env")
    db = create_engine(url, pool_pre_ping=True)
    if args.refresh_calendar:
        refresh_holiday_calendar(db)
    pipeline = ModuleBPipeline(db)
    if args.all:
        result = pipeline.scan(args.as_of, persist=args.persist)
        if not args.details:
            result["results"] = [
                r for r in result["results"] if r["signal"] or r["watchlist"]
            ]
            result["detail_hint"] = "Use --details for all rejection records."
    else:
        scanner = EarningsAnchoredSetup2(pipeline) if args.setup == "2" else pipeline
        row = scanner.evaluate(
            args.instrument.upper(), args.as_of, require_module_a=not args.research_only
        )
        if args.persist:
            pipeline.persist_results([row], pipeline._stamp(args.as_of))
        result = asdict(row)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
