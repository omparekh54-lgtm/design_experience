from __future__ import annotations
import argparse, json, os, sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine
from packages.pms.module_a_context import IST
from packages.pms.module_e_pipeline import ModuleEPipeline, compact_quality
from apps.api.main import _render_safe_database_url


def main():
    parser = argparse.ArgumentParser(
        description="Verified market regime and formal PAPER pending-entry controls."
    )
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Save regime and cancel/trim only formal PAPER pending buys to capacity",
    )
    parser.add_argument(
        "--details", action="store_true", help="Print all per-stock coverage exceptions"
    )
    args = parser.parse_args()
    if args.as_of is None:
        now = datetime.now(IST)
        args.as_of = (
            now.date()
            if now.hour * 60 + now.minute >= 15 * 60 + 30
            else now.date() - timedelta(days=1)
        )
    if args.as_of > datetime.now(IST).date():
        parser.error("Future dates are not supported")
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL/PMS_DATABASE_URL is required in .env")
    engine = create_engine(
        _render_safe_database_url(url),
        pool_pre_ping=True,
        connect_args={"options": "-c search_path=pms,public"},
    )
    pipeline = ModuleEPipeline(engine)
    pipeline.evaluate(args.as_of, persist=args.persist)
    report = pipeline.last_report
    if not args.details:
        report["data_quality"] = compact_quality(report["data_quality"])
    print(json.dumps(report, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
