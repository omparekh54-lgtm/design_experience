from __future__ import annotations

import os
import time
from datetime import date
from typing import Callable

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


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


def _industry_info(payload: dict) -> tuple[str | None, str | None]:
    info = payload.get("industryInfo") or payload.get("industryinfo") or {}
    sector = info.get("sector") or info.get("macro")
    industry = info.get("industry") or info.get("basicIndustry")
    return (sector.strip() if isinstance(sector, str) and sector.strip() else None,
            industry.strip() if isinstance(industry, str) and industry.strip() else None)


def ingest(progress: Callable[[int, int, str], None] | None = None, delay: float = 0.20) -> dict:
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            select id,nse_symbol
            from pms.instruments
            where active=true and exchange='NSE'
              and nse_symbol is not null
              and (sector is null or btrim(sector)='')
            order by nse_symbol
        """)).mappings().all()

    total = len(rows)
    done = 0
    updated = 0
    failed: list[str] = []
    session = _session()

    for row in rows:
        symbol = row["nse_symbol"]
        try:
            r = session.get("https://www.nseindia.com/api/quote-equity", params={"symbol": symbol}, timeout=20)
            if r.status_code in (401, 403):
                session = _session()
                r = session.get("https://www.nseindia.com/api/quote-equity", params={"symbol": symbol}, timeout=20)
            r.raise_for_status()
            sector, industry = _industry_info(r.json())
            if sector:
                with engine.begin() as conn:
                    conn.execute(text("""
                        update pms.instruments
                        set sector=:sector, updated_at=now()
                        where id=:instrument_id
                    """), {"sector": sector, "instrument_id": row["id"]})
                    conn.execute(text("""
                        insert into pms.sector_history(instrument_id,sector,industry,effective_from,effective_to,source)
                        values(:instrument_id,:sector,:industry,:effective_from,null,'NSE_QUOTE_EQUITY')
                        on conflict do nothing
                    """), {
                        "instrument_id": row["id"], "sector": sector, "industry": industry,
                        "effective_from": date.today(),
                    })
                updated += 1
        except Exception:
            failed.append(symbol)
        finally:
            done += 1
            if progress:
                progress(done, total, symbol)
            time.sleep(delay)

    return {"requested": total, "updated": updated, "failed": len(failed), "failed_symbols": failed[:50]}


if __name__ == "__main__":
    result = ingest(lambda n, t, s: print(f"\rSectors {n}/{t} {s:16}", end="", flush=True))
    print()
    print(result)
