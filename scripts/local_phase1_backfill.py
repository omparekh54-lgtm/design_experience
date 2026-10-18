from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

BAR = 34


def engine():
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        raise RuntimeError("Missing DATABASE_URL. Create .env from .env.example and paste your Supabase Session Pooler connection string.")
    return create_engine(url, pool_pre_ping=True)


def bar(pct: float) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round(BAR * pct / 100)
    return "[" + "#" * filled + "-" * (BAR - filled) + f"] {pct:5.1f}%"


def status_snapshot(db) -> dict:
    with db.connect() as conn:
        total, with_sector = conn.execute(text("""
            select count(*)::int,
                   count(*) filter(where sector is not null and btrim(sector)<>'')::int
            from pms.instruments where active=true and exchange='NSE'
        """)).one()
        bench = {r[0]: {"rows": int(r[1]), "min": r[2], "max": r[3]} for r in conn.execute(text("""
            select benchmark_code,count(*)::int,min(trade_date),max(trade_date)
            from pms.module_e_benchmark_bars
            where benchmark_code in ('NIFTYMIDSMALLCAP400','INDIAVIX')
            group by benchmark_code
        """)).all()}
        gsm = conn.execute(text("""
            select count(*)::int from pms.surveillance_history
            where surveillance_type='GSM' and effective_from < date '2024-01-23'
        """)).scalar_one()
        unresolved = conn.execute(text("""
            select count(*)::int from pms.data_quality_issues
            where severity='ERROR' and resolved_at is null
        """)).scalar_one()
    return {"total": total, "sector": with_sector, "bench": bench, "gsm": gsm, "errors": unresolved}


def date_coverage(item: dict | None) -> float:
    if not item or not item.get("min") or not item.get("max"):
        return 0.0
    start = date(2018, 1, 1)
    end = date.today()
    covered = max(0, (item["max"] - max(start, item["min"])).days)
    span = max(1, (end - start).days)
    return min(100.0, 100.0 * covered / span)


def percentages(s: dict) -> dict[str, float]:
    sector = 100.0 * s["sector"] / max(1, s["total"])
    mid = date_coverage(s["bench"].get("NIFTYMIDSMALLCAP400"))
    vix = date_coverage(s["bench"].get("INDIAVIX"))
    # Historical GSM is circular/event based rather than one row per day. Once
    # at least one reconstructed interval exists in every year, final audit is
    # what decides whether it is complete. Until then the live meter advances
    # conservatively by interval volume, capped below 100.
    gsm = min(99.0, s["gsm"] / 12.0) if s["gsm"] else 0.0
    return {"Sectors": sector, "MidSmallcap 400": mid, "India VIX": vix, "Pre-2024 GSM": gsm}


def dashboard(db, note: str = ""):
    s = status_snapshot(db)
    p = percentages(s)
    overall = sum(p.values()) / 4
    print("\n" + "=" * 62)
    print(" PMS PHASE-1 LOCAL DATA BACKFILL")
    print("=" * 62)
    print(f"Overall          {bar(overall)}")
    print(f"1. Sectors       {bar(p['Sectors'])}  {s['sector']}/{s['total']} stocks")
    mid = s['bench'].get('NIFTYMIDSMALLCAP400', {})
    vix = s['bench'].get('INDIAVIX', {})
    print(f"2. MidSmall 400  {bar(p['MidSmallcap 400'])}  {mid.get('rows',0)} rows")
    print(f"3. India VIX     {bar(p['India VIX'])}  {vix.get('rows',0)} rows")
    print(f"4. Pre-2024 GSM  {bar(p['Pre-2024 GSM'])}  {s['gsm']} intervals")
    print(f"Critical DQ errors still open: {s['errors']}")
    if note:
        print(f"\n{note}")
    print("=" * 62)
    return s, p


def main():
    db = engine()
    with db.connect() as conn:
        conn.execute(text("select 1"))
    dashboard(db, "Database connection OK. Starting remaining Phase-1 jobs...")

    from scripts.ingest_sector_classifications import ingest as ingest_sectors

    def sector_progress(n, total, symbol):
        pct = 100 * n / max(1, total)
        print(f"\r[1/4] Fetching NSE sectors {bar(pct)}  {n}/{total}  {symbol:16}", end="", flush=True)

    print("\n[1/4] Sector classifications")
    sector_result = ingest_sectors(sector_progress)
    print("\nSector result:", sector_result)
    dashboard(db, "Sector pass finished.")

    print("\n[2/4 + 3/4] Official Nifty MidSmallcap 400 + India VIX history")
    print("Fetching official history in yearly chunks. This step may sit on the same percentage for a few minutes.")
    import scripts.ingest_module_e_benchmarks as benchmark
    benchmark_result = benchmark.ingest(date(2018, 1, 1), date.today())
    print("Benchmark result:", benchmark_result)
    dashboard(db, "Benchmark/VIX pass finished.")

    print("\n[4/4] Pre-2024 GSM reconstruction from NSE circular archive")
    from scripts.ingest_legacy_gsm import ingest as ingest_gsm

    def gsm_progress(n, total, window):
        pct = 100 * n / max(1, total)
        print(f"\r[4/4] Reading GSM circulars {bar(pct)}  {n}/{total}  {window:28}", end="", flush=True)

    gsm_result = ingest_gsm(gsm_progress)
    print("\nGSM result:", gsm_result)
    final, p = dashboard(db, "All four fetchers have finished. Running final audit...")

    failures = []
    if final["sector"] < final["total"]:
        failures.append(f"sector coverage {final['sector']}/{final['total']}")
    for code in ("NIFTYMIDSMALLCAP400", "INDIAVIX"):
        item = final["bench"].get(code)
        if not item or not item.get("max") or (date.today() - item["max"]).days > 10:
            failures.append(f"{code} missing/stale")
    if final["gsm"] == 0:
        failures.append("pre-2024 GSM produced no intervals")
    if final["errors"]:
        failures.append(f"{final['errors']} unresolved ERROR data-quality issue(s)")

    print("\nFINAL AUDIT")
    if failures:
        print("NOT 100% YET. Remaining items:")
        for item in failures:
            print(" -", item)
        print("\nThe command is resumable: run it again after any source/network issue; existing rows are preserved.")
        raise SystemExit(2)
    print("PASS: Phase-1 mandatory data checks are complete.")


if __name__ == "__main__":
    main()
