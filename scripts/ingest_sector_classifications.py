from __future__ import annotations

import os
import time
from datetime import date
from typing import Callable, Any

from dotenv import load_dotenv
from nse import NSE
from sqlalchemy import create_engine, text

load_dotenv()


def _engine():
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(url, pool_pre_ping=True)


def _clean(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _industry_info(payload: dict) -> tuple[str | None, str | None]:
    """Read NSE's classification from both current and legacy response shapes."""
    if not isinstance(payload, dict):
        return None, None

    # Older quote-equity response shape.
    info = payload.get("industryInfo")
    if isinstance(info, dict):
        sector = _clean(info.get("sector")) or _clean(info.get("macro"))
        industry = _clean(info.get("industry")) or _clean(info.get("basicIndustry"))
        if sector:
            return sector, industry

    # NSE client 5.x simplified quote response.
    for key in ("metaData", "metadata"):
        meta = payload.get(key)
        if isinstance(meta, dict):
            sector = _clean(meta.get("sector")) or _clean(meta.get("macro"))
            industry = (
                _clean(meta.get("industryInfo"))
                or _clean(meta.get("industry"))
                or _clean(meta.get("basicIndustry"))
            )
            if sector:
                return sector, industry

    # Detailed NextApi response shape.
    equity = payload.get("equityResponse")
    if isinstance(equity, list) and equity and isinstance(equity[0], dict):
        first = equity[0]
        sec_info = first.get("secInfo") or {}
        if isinstance(sec_info, dict):
            sector = _clean(sec_info.get("sector")) or _clean(sec_info.get("macro"))
            industry = _clean(sec_info.get("industryInfo")) or _clean(sec_info.get("basicIndustry"))
            if sector:
                return sector, industry
        return _industry_info(first)

    # Defensive flattened fallback.
    sector = _clean(payload.get("sector")) or _clean(payload.get("macro"))
    industry = _clean(payload.get("industryInfo")) or _clean(payload.get("industry")) or _clean(payload.get("basicIndustry"))
    return sector, industry


def ingest(progress: Callable[[int, int, str], None] | None = None, delay: float = 0.40) -> dict:
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
    first_errors: list[str] = []

    # nse 5.x manages the current NSE request flow, cookies and throttling.
    with NSE(download_folder=".") as nse:
        for done, row in enumerate(rows, start=1):
            symbol = row["nse_symbol"]
            try:
                payload = nse.equity_quote(symbol)
                sector, industry = _industry_info(payload)
                if not sector:
                    raise RuntimeError("classification missing in NSE quote response")

                with engine.begin() as conn:
                    conn.execute(text("""
                        update pms.instruments
                        set sector=:sector, updated_at=now()
                        where id=:instrument_id
                    """), {"sector": sector, "instrument_id": row["id"]})
                    conn.execute(text("""
                        insert into pms.sector_history(instrument_id,sector,industry,effective_from,effective_to,source)
                        select :instrument_id,:sector,:industry,:effective_from,null,'NSE_EQUITY_QUOTE_V5'
                        where not exists (
                            select 1 from pms.sector_history
                            where instrument_id=:instrument_id
                              and sector=:sector
                              and effective_to is null
                        )
                    """), {
                        "instrument_id": row["id"],
                        "sector": sector,
                        "industry": industry,
                        "effective_from": date.today(),
                    })
                updated += 1
            except Exception as exc:
                failed.append(symbol)
                if len(first_errors) < 5:
                    first_errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            finally:
                if progress:
                    progress(done, total, symbol)
                time.sleep(delay)

    return {
        "requested": total,
        "updated": updated,
        "failed": len(failed),
        "failed_symbols": failed[:50],
        "sample_errors": first_errors,
    }


if __name__ == "__main__":
    result = ingest(lambda n, t, s: print(f"\rSectors {n}/{t} {s:16}", end="", flush=True))
    print()
    print(result)
