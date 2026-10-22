from __future__ import annotations

import argparse
import json
import os
import math
import sys
import time
from datetime import date, datetime, timedelta
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
CALENDAR_CODE = "NIFTY50"
REQUIRED_CODES = ("NIFTY50", "NIFTY500", "NIFTYMIDSMALLCAP400", "INDIAVIX")


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
        number = float(str(value).replace(",", "").strip())
        return number if math.isfinite(number) else None
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
        if d is None or close is None or close <= 0:
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
        if d is None or close is None or close <= 0:
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


def month_windows(start: date, end: date):
    if start > end:
        raise ValueError("start must not be after end")
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(end, next_month - timedelta(days=1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def fetch_window(nse, code: str, start: date, end: date) -> tuple[list[dict], dict]:
    if code in {"NIFTYMIDSMALLCAP400", CALENDAR_CODE, "NIFTY500"}:
        name = {"NIFTYMIDSMALLCAP400":INDEX_NAME, CALENDAR_CODE:"NIFTY 50", "NIFTY500":"NIFTY 500"}[code]
        raw = nse.fetch_historical_index_data(name, from_date=start, to_date=end)
        rows = _normalise_index(raw)
        for row in rows:
            row["benchmark_code"] = code
    elif code == "INDIAVIX":
        raw = nse.fetch_historical_vix_data(from_date=start, to_date=end)
        rows = _normalise_vix(raw)
    else:
        raise ValueError(f"Unknown benchmark {code}")
    if any(not start <= row["trade_date"] <= end for row in rows):
        raise RuntimeError(f"{code}: source returned dates outside {start}..{end}")
    if len(rows) != len(raw):
        raise RuntimeError(f"{code}: rejected {len(raw)-len(rows)} invalid rows in {start}..{end}")
    unique = {row["trade_date"]: row for row in rows}
    return list(unique.values()), {"code": code, "start": str(start), "end": str(end),
                                   "raw_rows": len(raw), "unique_dates": len(unique)}


def _fetch_retry(cache: Path, code: str, start: date, end: date, attempts: int = 5):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            # Fresh NSE session per attempt: a socket ReadTimeout can leave a
            # long-lived session unhealthy on Windows.
            with NSE(download_folder=cache) as nse:
                return fetch_window(nse, code, start, end)
        except Exception as exc:
            last = exc
            if attempt == attempts:
                break
            delay = min(20, 2 ** (attempt - 1))
            print(f"  RETRY: {code} {start}..{end} after {type(exc).__name__}; {delay}s", flush=True)
            time.sleep(delay)
    assert last is not None
    raise last


def _dates(engine, code: str, start: date, end: date) -> set[date]:
    with engine.connect() as conn:
        return set(conn.execute(text("""
            select distinct trade_date from pms.module_e_benchmark_bars
            where benchmark_code=:code
              and source in ('NSE_HISTORICAL_OR','NSE_DAILY_INDEX')
              and trade_date between :start and :end
        """), {"code": code, "start": start, "end": end}).scalars())


def coverage_audit(engine, start: date, end: date) -> dict:
    expected = _dates(engine, CALENDAR_CODE, start, end)
    calendar_complete = bool(expected) and (end-max(expected)).days <= 7
    for first, last in month_windows(start, end):
        if (last-first).days >= 7 and not any(first <= d <= last for d in expected):
            calendar_complete = False
    result = {}
    for code in REQUIRED_CODES:
        observed = _dates(engine, code, start, end)
        missing = sorted(expected - observed)
        latest = max(observed) if observed else None
        result[code] = {
            "rows": len(observed), "expected_sessions": len(expected),
            "matched_sessions": len(expected & observed),
            "missing_sessions": len(missing), "missing_dates": [str(d) for d in missing],
            "min_date": min(observed) if observed else None, "max_date": latest,
            "stale": latest is None or (end-latest).days > 7,
            "calendar_unavailable": not calendar_complete,
            "calendar_source": "OFFICIAL_NSE:NIFTY50",
            "unexpected_dates": [str(d) for d in sorted(observed-expected)],
        }
    return result


def ingest(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    engine = _engine()
    start = start or date(2016, 11, 1)
    end = end or date.today()
    cache = ROOT / ".cache_nse"
    cache.mkdir(exist_ok=True)
    counts = {"index": 0, "vix": 0, "calendar": 0, "nifty500": 0, "skipped_complete": 0}
    diagnostics = []
    failures = []
    windows = list(month_windows(start, end))
    try:
        for n, (first, last) in enumerate(windows, 1):
            try:
                calendar_rows, detail = _fetch_retry(cache, "NIFTY50", first, last)
                counts["calendar"] += upsert_rows(engine, calendar_rows)
                diagnostics.append(detail)
                expected = {r["trade_date"] for r in calendar_rows}
                if not expected:
                    raise RuntimeError("official NIFTY50 month returned no sessions")
                print(f"  [{n}/{len(windows)}] NIFTY50 {first}..{last}: {len(calendar_rows)} dates", flush=True)
            except Exception as exc:
                failures.append(f"NIFTY50 {first}..{last}: {type(exc).__name__}: {exc}")
                print(f"  FAILED: {failures[-1]}", flush=True)
                continue

            for code, label in (("NIFTY500", "nifty500"), ("NIFTYMIDSMALLCAP400", "index"), ("INDIAVIX", "vix")):
                try:
                    existing = _dates(engine, code, first, last)
                    if expected.issubset(existing):
                        counts["skipped_complete"] += 1
                        print(f"  [{n}/{len(windows)}] {code} {first}..{last}: already complete", flush=True)
                        continue
                    rows, detail = _fetch_retry(cache, code, first, last)
                    counts[label] += upsert_rows(engine, rows)
                    diagnostics.append(detail)
                    observed = _dates(engine, code, first, last)
                    missing = expected - observed
                    if missing:
                        raise RuntimeError(f"still missing {len(missing)} NIFTY50 sessions after fetch")
                    print(f"  [{n}/{len(windows)}] {code} {first}..{last}: {len(rows)} dates", flush=True)
                except Exception as exc:
                    failures.append(f"{code} {first}..{last}: {type(exc).__name__}: {exc}")
                    print(f"  FAILED: {failures[-1]}", flush=True)
        audit = coverage_audit(engine, start, end)
        if failures or any(v["missing_sessions"] or v["stale"] or v["calendar_unavailable"] for v in audit.values()):
            raise RuntimeError(json.dumps({"written": counts, "coverage": audit, "failed_windows": failures}, default=str))
        return {"written": counts, "coverage": audit, "windows": diagnostics}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(ingest(args.start, args.end), default=str, indent=2))


if __name__ == "__main__":
    main()
