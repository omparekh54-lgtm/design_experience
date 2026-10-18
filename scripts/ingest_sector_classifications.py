from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from nse import NSE
from sqlalchemy import create_engine, text

load_dotenv()


def _engine():
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(url, pool_pre_ping=True)


def _classification(payload: dict) -> tuple[str | None, str | None]:
    """Extract NSE 4-tier classification from the current Next API payload."""
    eq = None
    if isinstance(payload, dict):
        rows = payload.get("equityResponse")
        if isinstance(rows, list) and rows:
            eq = rows[0]
        elif isinstance(payload.get("secInfo"), dict):
            eq = payload
    if not isinstance(eq, dict):
        return None, None

    sec = eq.get("secInfo") or eq.get("securityInfo") or {}
    if not isinstance(sec, dict):
        return None, None

    sector = sec.get("sector") or sec.get("macro")
    industry = sec.get("industryInfo") or sec.get("industry") or sec.get("basicIndustry")
    sector = sector.strip() if isinstance(sector, str) and sector.strip() else None
    industry = industry.strip() if isinstance(industry, str) and industry.strip() else None
    return sector, industry


def ingest(progress: Callable[[int, int, str], None] | None = None, delay: float = 0.05) -> dict:
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
    updated = 0
    failed: list[str] = []
    errors: list[str] = []
    download_dir = Path(".cache_nse")
    download_dir.mkdir(exist_ok=True)

    # The nse package handles cookies, current Next API routing, throttling and retries.
    with NSE(download_folder=download_dir) as nse:
        for done, row in enumerate(rows, start=1):
            symbol = row["nse_symbol"]
            try:
                payload = nse.get_detailed_scrip_data(symbol)
                sector, industry = _classification(payload)
                if not sector:
                    raise RuntimeError("classification missing in NSE Next API response")
                with engine.begin() as conn:
                    conn.execute(text("""
                        update pms.instruments
                        set sector=:sector, updated_at=now()
                        where id=:instrument_id
                    """), {"sector": sector, "instrument_id": row["id"]})
                    conn.execute(text("""
                        insert into pms.sector_history(instrument_id,sector,industry,effective_from,effective_to,source)
                        values(:instrument_id,:sector,:industry,:effective_from,null,'NSE_NEXT_API')
                        on conflict do nothing
                    """), {
                        "instrument_id": row["id"], "sector": sector, "industry": industry,
                        "effective_from": date.today(),
                    })
                updated += 1
            except Exception as exc:
                failed.append(symbol)
                if len(errors) < 10:
                    errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            finally:
                if progress:
                    progress(done, total, symbol)
                if delay:
                    time.sleep(delay)

    return {
        "requested": total,
        "updated": updated,
        "failed": len(failed),
        "failed_symbols": failed[:50],
        "sample_errors": errors,
    }


if __name__ == "__main__":
    result = ingest(lambda n, t, s: print(f"\rSectors {n}/{t} {s:16}", end="", flush=True))
    print()
    print(result)
