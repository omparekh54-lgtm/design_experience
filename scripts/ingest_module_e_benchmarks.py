from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from nse import NSE
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url

load_dotenv(ROOT / ".env")
SOURCE = "NSE_HISTORICAL_OR"
INDEX_NAME = "NIFTY MIDSMALLCAP 400"


def _engine():
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(_render_safe_database_url(raw), pool_pre_ping=True)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    txt = str(value).strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _normalise_index(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        d = _parse_date(_first(row, "EOD_TIMESTAMP", "HistoricalDate", "DATE", "Date", "date"))
        close = _number(_first(row, "EOD_CLOSE_INDEX_VAL", "CLOSE", "close"))
        if d is None or close is None:
            continue
        out.append({
            "benchmark_code": "NIFTYMIDSMALLCAP400",
            "trade_date": d,
            "open": _number(_first(row, "EOD_OPEN_INDEX_VAL", "OPEN", "open")),
            "high": _number(_first(row, "EOD_HIGH_INDEX_VAL", "HIGH", "high")),
            "low": _number(_first(row, "EOD_LOW_INDEX_VAL", "LOW", "low")),
            "close": close,
            "source": SOURCE,
            "raw_payload": json.dumps(row, default=str),
        })
    return out


def _normalise_vix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        d = _parse_date(_first(row, "EOD_TIMESTAMP", "date", "DATE", "Date"))
        close = _number(_first(row, "EOD_CLOSE_INDEX_VAL", "EOD_CLOSE", "close", "CLOSE"))
        if d is None or close is None:
            continue
        out.append({
            "benchmark_code": "INDIAVIX",
            "trade_date": d,
            "open": _number(_first(row, "EOD_OPEN_INDEX_VAL", "EOD_OPEN", "open", "OPEN")),
            "high": _number(_first(row, "EOD_HIGH_INDEX_VAL", "EOD_HIGH", "high", "HIGH")),
            "low": _number(_first(row, "EOD_LOW_INDEX_VAL", "EOD_LOW", "low", "LOW")),
            "close": close,
            "source": SOURCE,
            "raw_payload": json.dumps(row, default=str),
        })
    return out


def upsert_rows(engine, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = text("""
        insert into pms.module_e_benchmark_bars(
            benchmark_code,trade_date,open,high,low,close,source,raw_payload
        ) values (
            :benchmark_code,:trade_date,:open,:high,:low,:close,:source,cast(:raw_payload as jsonb)
        )
        on conflict (benchmark_code,trade_date,source)
        do update set open=excluded.open,high=excluded.high,low=excluded.low,
                      close=excluded.close,raw_payload=excluded.raw_payload
    """)
    with engine.begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def ingest(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    engine = _engine()
    start = start or date(2018, 1, 1)
    end = end or date.today()
    cache = ROOT / ".cache_nse"
    cache.mkdir(exist_ok=True)

    with NSE(download_folder=cache) as nse:
        index_raw = nse.fetch_historical_index_data(INDEX_NAME, from_date=start, to_date=end)
        vix_raw = nse.fetch_historical_vix_data(from_date=start, to_date=end)

    index_rows = _normalise_index(index_raw)
    vix_rows = _normalise_vix(vix_raw)
    counts = {
        "index": upsert_rows(engine, index_rows),
        "vix": upsert_rows(engine, vix_rows),
    }

    with engine.connect() as conn:
        health = conn.execute(text("""
            select benchmark_code,count(*) rows,min(trade_date) min_date,max(trade_date) max_date
            from pms.module_e_benchmark_bars
            where benchmark_code in ('NIFTYMIDSMALLCAP400','INDIAVIX')
            group by benchmark_code order by benchmark_code
        """)).mappings().all()

    if counts["index"] == 0:
        raise RuntimeError("NSE returned no usable Nifty MidSmallcap 400 rows")
    if counts["vix"] == 0:
        raise RuntimeError("NSE returned no usable India VIX rows")
    return {"written": counts, "coverage": [dict(row) for row in health]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(ingest(args.start, args.end), default=str, indent=2))


if __name__ == "__main__":
    main()
