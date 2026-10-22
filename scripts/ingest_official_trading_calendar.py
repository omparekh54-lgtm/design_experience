from __future__ import annotations

"""Fetch and persist the official NSE trading-holiday calendar only.

This is intentionally narrower than CloudCollector.core(): Step-1 finalization
needs independent calendar provenance but must not trigger unrelated daily jobs.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP
from packages.data_layer.cloud_storage import CloudStore
from packages.pms.market_calendar import parse_holiday_calendar


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=date.today().year)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    engine = create_engine(
        _render_safe_database_url(raw), pool_pre_ping=True,
        connect_args={"options": "-c search_path=pms,public"},
    )
    try:
        http = OfficialHTTP(seconds=180)
        doc = http.api("holiday-master", type="trading")
        calendar = parse_holiday_calendar(json.loads(doc.body))
        if str(args.year) not in calendar.get("holiday_years", {}):
            raise RuntimeError(f"official trading calendar does not include {args.year}")
        CloudStore(engine).calendar(calendar, doc)
        with engine.connect() as conn:
            row = conn.execute(text("""
              select content_sha256,source_uri,observed_at
              from pms.provider_snapshots
              where dataset='NSE_TRADING_CALENDAR'
              order by observed_at desc,id desc limit 1
            """)).mappings().one_or_none()
        if not row or not row["source_uri"] or not row["content_sha256"]:
            raise RuntimeError("trading calendar receipt was not persisted")
        print(json.dumps({"status":"PASS","year":args.year,
                          "source_uri":row["source_uri"],
                          "content_sha256":row["content_sha256"],
                          "observed_at":row["observed_at"]},default=str,indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
