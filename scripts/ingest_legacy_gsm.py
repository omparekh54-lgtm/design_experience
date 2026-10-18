from __future__ import annotations

import io
import os
import re
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable

import requests
from dotenv import load_dotenv
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import create_engine, text

load_dotenv()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
SOURCE = "NSE_LEGACY_GSM_LOCAL"


def _engine():
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(url, pool_pre_ping=True)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com/", timeout=20)
    return s


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    txt = str(value).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    return None


def _attachment_urls(obj) -> list[str]:
    urls: list[str] = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
        elif isinstance(x, str) and x.startswith("http") and any(ext in x.lower() for ext in (".zip", ".xlsx", ".xls", ".pdf")):
            urls.append(x)
    walk(obj)
    return list(dict.fromkeys(urls))


def _symbols_from_xlsx(data: bytes, known: set[str]) -> set[str]:
    out: set[str] = set()
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for value in row:
                if not isinstance(value, str):
                    continue
                token = value.strip().upper()
                if token in known:
                    out.add(token)
                else:
                    for part in re.findall(r"[A-Z0-9&.-]{2,25}", token):
                        if part in known:
                            out.add(part)
    return out


def _symbols_from_pdf(data: bytes, known: set[str]) -> set[str]:
    out: set[str] = set()
    try:
        reader = PdfReader(io.BytesIO(data))
        text_all = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return out
    tokens = set(re.findall(r"[A-Z0-9&.-]{2,25}", text_all.upper()))
    return tokens.intersection(known)


def _extract_attachment(session: requests.Session, url: str, known: set[str]) -> set[str]:
    r = session.get(url, timeout=40)
    r.raise_for_status()
    data = r.content
    lower = url.lower()
    if lower.endswith(".zip") or data[:2] == b"PK":
        out: set[str] = set()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                nl = name.lower()
                raw = zf.read(name)
                if nl.endswith(".xlsx"):
                    out |= _symbols_from_xlsx(raw, known)
                elif nl.endswith(".pdf"):
                    out |= _symbols_from_pdf(raw, known)
        return out
    if lower.endswith(".xlsx"):
        return _symbols_from_xlsx(data, known)
    if lower.endswith(".pdf"):
        return _symbols_from_pdf(data, known)
    return set()


def _subject(row: dict) -> str:
    for key in ("subject", "sub", "circSubject", "circularSubject", "title"):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def _circular_date(row: dict) -> date | None:
    for key in ("circDate", "date", "circularDate", "displayDate"):
        d = _parse_date(row.get(key))
        if d:
            return d
    return None


def _fetch_circulars(session: requests.Session, start: date, end: date) -> list[dict]:
    params = {"from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y"), "sub": "GSM"}
    r = session.get("https://www.nseindia.com/api/circulars", params=params, timeout=30)
    if r.status_code in (401, 403):
        session.get("https://www.nseindia.com/", timeout=20)
        r = session.get("https://www.nseindia.com/api/circulars", params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, list):
        return payload
    for key in ("data", "circulars", "records"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def ingest(progress: Callable[[int, int, str], None] | None = None) -> dict:
    engine = _engine()
    with engine.connect() as conn:
        instrument_rows = conn.execute(text("select id,nse_symbol from pms.instruments where active=true and exchange='NSE' and nse_symbol is not null")).mappings().all()
    by_symbol = {r["nse_symbol"].upper(): r["id"] for r in instrument_rows}
    known = set(by_symbol)

    session = _session()
    events: dict[str, list[tuple[date, bool]]] = defaultdict(list)
    chunks = []
    cursor = date(2018, 1, 1)
    end_all = date(2023, 12, 31)
    while cursor <= end_all:
        chunk_end = min(cursor + timedelta(days=89), end_all)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)

    processed = 0
    circular_count = 0
    attachment_count = 0
    for start, end in chunks:
        try:
            rows = _fetch_circulars(session, start, end)
        except Exception:
            rows = []
        for row in rows:
            subject = _subject(row)
            if "GSM" not in subject.upper() and "GRADED SURVEILLANCE" not in subject.upper():
                continue
            cdate = _circular_date(row) or start
            urls = _attachment_urls(row)
            symbols: set[str] = set()
            for url in urls:
                try:
                    symbols |= _extract_attachment(session, url, known)
                    attachment_count += 1
                except Exception:
                    pass
            if not symbols:
                continue
            circular_count += 1
            s = subject.lower()
            inactive = any(x in s for x in ("moving out", "move out", "removed", "removal", "exclusion", "exit from gsm"))
            for symbol in symbols:
                events[symbol].append((cdate, not inactive))
        processed += 1
        if progress:
            progress(processed, len(chunks), f"{start}..{end}")
        time.sleep(0.25)

    intervals = []
    for symbol, evs in events.items():
        evs = sorted(set(evs))
        active_from: date | None = None
        for d, active in evs:
            if active and active_from is None:
                active_from = d
            elif not active and active_from is not None:
                intervals.append((by_symbol[symbol], active_from, d - timedelta(days=1), symbol))
                active_from = None
        if active_from is not None:
            intervals.append((by_symbol[symbol], active_from, date(2024, 1, 22), symbol))

    with engine.begin() as conn:
        for instrument_id, start, end, symbol in intervals:
            conn.execute(text("""
                insert into pms.surveillance_history(instrument_id,surveillance_type,stage,effective_from,effective_to,source)
                values(:instrument_id,'GSM','ACTIVE',:effective_from,:effective_to,:source)
                on conflict do nothing
            """), {"instrument_id": instrument_id, "effective_from": start, "effective_to": end, "source": SOURCE})

    return {"circulars_used": circular_count, "attachments_parsed": attachment_count, "symbols": len(events), "intervals_written": len(intervals)}


if __name__ == "__main__":
    result = ingest(lambda n, t, s: print(f"\rLegacy GSM {n}/{t} {s:28}", end="", flush=True))
    print()
    print(result)
