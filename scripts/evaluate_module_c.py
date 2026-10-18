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
from packages.pms.module_c_pipeline import ModuleCPipeline
from apps.api.main import _render_safe_database_url


def main():
    parser = argparse.ArgumentParser(
        description="After-close PAPER readiness rankings and order proposals; no broker orders."
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=datetime.now(IST).date()
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    if args.as_of > datetime.now(IST).date():
        parser.error("Future dates are not supported")
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("PGOPTIONS", "-c search_path=pms,public")
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL/PMS_DATABASE_URL is required in .env")
    engine = create_engine(_render_safe_database_url(url), pool_pre_ping=True)
    result = asdict(ModuleCPipeline(engine).generate(args.as_of, persist=args.persist))
    if not args.details:
        result.pop("results")
    result["broker_orders_submitted"] = 0
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
