from __future__ import annotations

import os
import time
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from nse import NSE
from sqlalchemy import create_engine, text

load_dotenv()

# Exchange circulars verified against detailed quote ISINs; aliases are used
# for classification lookup only, never to rewrite historical identifiers.
SYMBOL_ALIASES = {
    "MANBRO": {"symbol": "KDGREEN", "effective_from": "2026-09-03",
               "source_url": "https://nsearchives.nseindia.com/content/circulars/CML76019.pdf"},
    "SILLYMONKS": {"symbol": "CRESTO", "effective_from": "2026-09-10",
                  "source_url": "https://nsearchives.nseindia.com/content/circulars/CML76169.pdf"},
}


def _engine():
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(url, pool_pre_ping=True)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.upper() not in {"", "NA", "N/A", "-", "NOT AVAILABLE", "UNKNOWN"} else None


def _classification(payload: dict) -> tuple[str | None, str | None]:
    """Accept Next API and legacy NSE classification shapes, never placeholders."""
    if not isinstance(payload, dict):
        return None, None
    rows = payload.get("equityResponse")
    eq = rows[0] if isinstance(rows, list) and rows else payload
    if not isinstance(eq, dict):
        return None, None
    sec = eq.get("secInfo") or eq.get("securityInfo") or eq.get("industryInfo") or {}
    if not isinstance(sec, dict):
        return None, None
    sector = _text(sec.get("sector")) or _text(sec.get("macro"))
    industry = _text(sec.get("industryInfo")) or _text(sec.get("industry")) or _text(sec.get("basicIndustry"))
    return sector, industry


def fetch_classification(nse, symbol: str, isin: str | None = None) -> dict:
    # EQ is not universal: trade-to-trade securities can be BE/BZ, SME SM/ST.
    alias = SYMBOL_ALIASES.get(symbol.upper())
    if alias and date.today() >= date.fromisoformat(alias["effective_from"]):
        result = fetch_classification(nse, alias["symbol"], isin)
        result["requested_symbol"] = symbol.upper()
        result["quote_symbol"] = alias["symbol"]
        result["symbol_alias"] = alias
        return result
    meta = nse.equity_meta_info(symbol)
    if not isinstance(meta, dict):
        raise RuntimeError("NSE metadata is not an object")
    metadata_symbol = str(meta.get("symbol") or "").upper()
    if metadata_symbol and metadata_symbol != symbol.upper():
        raise RuntimeError("NSE metadata symbol mismatch")
    # Classification remains useful for suspended instruments, without making
    # them tradable. Keep suspension state in provenance for eligibility review.
    active = meta.get("activeSeries") or []
    suspended = meta.get("tempSuspendedSeries") or []
    series = list(dict.fromkeys(active + suspended))
    # Some symbols have an empty getMetaData response while the quote exists.
    # Only accept a fallback quote if BOTH its symbol and ISIN match the DB.
    if not series:
        series = ["EQ", "BE", "BZ", "SM", "ST"]
    market_type = str(meta.get("marketType") or "N")
    for item in series:
        try:
            payload = nse.get_detailed_scrip_data(symbol, series=str(item), market_type=market_type)
        except Exception:
            if len(series) == 1:
                raise
            continue
        quote_rows = payload.get("equityResponse", []) if isinstance(payload, dict) else []
        quote_meta = (quote_rows[0].get("metaData") or {}) if quote_rows and isinstance(quote_rows[0], dict) else {}
        if not quote_meta:
            continue
        if str(quote_meta.get("symbol") or "").upper() != symbol.upper():
            raise RuntimeError("NSE quote symbol mismatch")
        # getMetaData can retain an old pre-split ISIN. The detailed quote
        # must match the stored current ISIN; never change identifiers here.
        if isin and str(quote_meta.get("isinCode") or "").upper() != isin.upper():
            raise RuntimeError("NSE quote ISIN mismatch")
        sector, industry = _classification(payload)
        if sector:
            return {"sector": sector, "industry": industry, "series": str(item).upper(),
                    "market_type": market_type, "isin": quote_meta.get("isinCode"), "source": "NSE_NEXT_API",
                    "metadata_isin": meta.get("isin"), "is_suspended": meta.get("isSuspended"),
                    "observed_at": datetime.now(timezone.utc).isoformat()}
    raise RuntimeError(f"classification missing for active NSE series {series}")


def ingest(progress: Callable[[int, int, str], None] | None = None, delay: float = 0.05) -> dict:
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            select id,nse_symbol,isin
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
                classification = fetch_classification(nse, symbol, row["isin"])
                sector, industry = classification["sector"], classification["industry"]
                with engine.begin() as conn:
                    conn.execute(text("""
                        update pms.instruments
                        set sector=:sector, updated_at=now(),
                            metadata=coalesce(metadata,'{}'::jsonb) || jsonb_build_object(
                                'sector_classification',cast(:classification as jsonb))
                        where id=:instrument_id
                    """), {"sector": sector, "instrument_id": row["id"], "classification": json.dumps(classification)})
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
        "failed_symbols": failed,
        "sample_errors": errors,
    }


def ingest_historical_sector_intervals() -> None:
    """Run the separate PIT importer only for CLI use.

    Imported current classifications remain dated today. Historical intervals
    come from dated NSE sector-index membership; this function never backdates
    the current classification table.
    """
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable,
        str(root / "scripts" / "backfill_historical_sector_intervals.py"),
        "--start", "2017-01-01",
        "--end", "2024-12-31",
    ], cwd=root)
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    result = ingest(lambda n, t, s: print(f"\rSectors {n}/{t} {s:16}", end="", flush=True))
    print()
    print(result)
    print("Loading dated historical sector intervals from point-in-time NSE sector-index membership...")
    ingest_historical_sector_intervals()
