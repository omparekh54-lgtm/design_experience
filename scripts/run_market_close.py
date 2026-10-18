"""Daily PAPER decisions. Data ingestion must finish before this command."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine
from apps.api.main import _render_safe_database_url
from packages.pms.daily_orchestrator import DailyOrchestrator


def engine_from_env():
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required in .env")
    return create_engine(
        _render_safe_database_url(raw),
        pool_pre_ping=True,
        connect_args={"options": "-c search_path=pms,public"},
    )


def run(trade_date: date | None = None, *, check_only=False, rerun=False):
    engine = engine_from_env()
    try:
        return DailyOrchestrator(engine).run(
            trade_date, check_only=check_only, rerun=rerun
        )
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="Persisted runs require today's IST date",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only calendar/data preflight; no run or module writes",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Refresh a successful current-day run; blocked/failed runs retry automatically",
    )
    args = parser.parse_args(argv)
    try:
        result = run(args.as_of, check_only=args.check, rerun=args.rerun)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, default=str, indent=2, allow_nan=False))
    # A blocked scheduled run must be visible as a non-successful job.
    return {"FAILED": 1, "BLOCKED": 2, "BUSY": 3}.get(result["status"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
