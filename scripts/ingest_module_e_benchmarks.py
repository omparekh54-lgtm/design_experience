from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url

BASE = "https://www.niftyindices.com"
HISTORICAL_URL = f"{BASE}/Backpage.aspx/getHistoricaldatatabletoString"
VIX_URL = f"{BASE}/Backpage.aspx/BindHistoricalIndiaVixData"
LANDING_URL = f"{BASE}/reports/historical-data"
INDEX_NAME = "NIFTY MIDSMALLCAP 400"
SOURCE = "NIFTY_INDICES_OFFICIAL"
HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": LANDING_URL,
    "Origin": BASE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    ),
}


def _engine():
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(_render_safe_database_url(raw), pool_pre_ping=True)


def _format_date(d: date) -> str:
    return d.strftime("%d %b %Y")


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text_value = str(value).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_value, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _double_json(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    body = response.text.lstrip()
    if body.startswith("<") or "text/html" in content_type:
        raise RuntimeError("Nifty Indices returned HTML instead of historical JSON")
    outer = response.json()
    inner = outer.get("d") if isinstance(outer, dict) else None
    if inner in (None, False, "false", "False", "[]"):
        return []
    parsed = json.loads(inner) if isinstance(inner, str) else inner
    if not isinstance(parsed, list):
        raise RuntimeError("Unexpected Nifty Indices response shape")
    return [row for row in parsed if isinstance(row, dict)]


def _client() -> httpx.Client:
    # Do not make a request here. The returned client is opened by the caller's
    # context manager. Making a request before `with client:` causes httpx to
    # reject the second open on local Python/httpx versions.
    return httpx.Client(headers=HEADERS, timeout=httpx.Timeout(60.0), follow_redirects=True)


def _warm(client: httpx.Client) -> None:
    try:
        client.get(LANDING_URL, timeout=15.0)
    except httpx.HTTPError:
        pass


def _post_with_retry(client: httpx.Client, url: str, *, content: str, attempts: int = 3) -> list[dict[str, Any]]:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.post(url, content=content)
            return _double_json(response)
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                _warm(client)
    raise RuntimeError(f"official benchmark fetch failed after {attempts} attempts: {error}") from error


def fetch_index(client: httpx.Client, start: date, end: date) -> list[dict[str, Any]]:
    cinfo = (
        "{'name':'" + INDEX_NAME + "','startDate':'" + _format_date(start)
        + "','endDate':'" + _format_date(end) + "','indexName':'" + INDEX_NAME + "'}"
    )
    return _post_with_retry(client, HISTORICAL_URL, content=json.dumps({"cinfo": cinfo}))


def fetch_vix(client: httpx.Client, start: date, end: date) -> list[dict[str, Any]]:
    payload = "{'name':'India VIX','startDate':'" + _format_date(start) + "','endDate':'" + _format_date(end) + "'}"
    return _post_with_retry(client, VIX_URL, content=payload)


def _chunks(start: date, end: date) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year, 12, 31), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _normalise_index(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        d = _parse_date(row.get("HistoricalDate") or row.get("DATE") or row.get("Date") or row.get("date"))
        close = _number(row.get("CLOSE") or row.get("close"))
        if d is None or close is None:
            continue
        out.append({
            "benchmark_code": "NIFTYMIDSMALLCAP400", "trade_date": d,
            "open": _number(row.get("OPEN") or row.get("open")),
            "high": _number(row.get("HIGH") or row.get("high")),
            "low": _number(row.get("LOW") or row.get("low")),
            "close": close, "source": SOURCE,
            "raw_payload": json.dumps(row, default=str),
        })
    return out


def _normalise_vix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        d = _parse_date(row.get("date") or row.get("DATE") or row.get("Date"))
        close = _number(row.get("close") or row.get("CLOSE"))
        if d is None or close is None:
            continue
        out.append({
            "benchmark_code": "INDIAVIX", "trade_date": d,
            "open": _number(row.get("open") or row.get("OPEN")),
            "high": _number(row.get("high") or row.get("HIGH")),
            "low": _number(row.get("low") or row.get("LOW")),
            "close": close, "source": SOURCE,
            "raw_payload": json.dumps(row, default=str),
        })
    return out


def upsert_rows(engine, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statement = text("""
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
        conn.execute(statement, rows)
    return len(rows)


def default_start(engine, code: str) -> date:
    with engine.connect() as conn:
        last = conn.execute(
            text("select max(trade_date) from pms.module_e_benchmark_bars where benchmark_code=:code and source=:source"),
            {"code": code, "source": SOURCE},
        ).scalar_one_or_none()
    if last is None:
        return date(2018, 1, 1)
    return max(date(2018, 1, 1), last - timedelta(days=10))


def ingest(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    engine = _engine()
    end = end or date.today()
    index_start = start or default_start(engine, "NIFTYMIDSMALLCAP400")
    vix_start = start or default_start(engine, "INDIAVIX")
    counts = {"index": 0, "vix": 0}

    with _client() as client:
        _warm(client)
        for chunk_start, chunk_end in _chunks(index_start, end):
            counts["index"] += upsert_rows(engine, _normalise_index(fetch_index(client, chunk_start, chunk_end)))
        for chunk_start, chunk_end in _chunks(vix_start, end):
            counts["vix"] += upsert_rows(engine, _normalise_vix(fetch_vix(client, chunk_start, chunk_end)))

    with engine.connect() as conn:
        health = conn.execute(text("""
            select benchmark_code,count(*) rows,min(trade_date) min_date,max(trade_date) max_date
            from pms.module_e_benchmark_bars
            where benchmark_code in ('NIFTYMIDSMALLCAP400','INDIAVIX')
            group by benchmark_code order by benchmark_code
        """)).mappings().all()
    return {"written": counts, "coverage": [dict(row) for row in health]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(ingest(args.start, args.end), default=str, indent=2))


if __name__ == "__main__":
    main()
