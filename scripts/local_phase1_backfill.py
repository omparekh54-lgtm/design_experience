from __future__ import annotations

import os
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
os.environ.setdefault("PGOPTIONS", "-c search_path=pms,public")

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
    from scripts.ingest_module_e_benchmarks import coverage_audit
    coverage = coverage_audit(db, date(2018, 1, 1), date.today())
    for code, item in coverage.items():
        bench.setdefault(code, {}).update(item)
    return {"total": total, "sector": with_sector, "bench": bench, "gsm": gsm, "errors": unresolved}


def date_coverage(item: dict | None) -> float:
    if not item or not item.get("expected_sessions"):
        return 0.0
    return 100.0 * item.get("matched_sessions", 0) / item["expected_sessions"]


def percentages(s: dict) -> dict[str, float]:
    sector = 100.0 * s["sector"] / max(1, s["total"])
    mid = date_coverage(s["bench"].get("NIFTYMIDSMALLCAP400"))
    vix = date_coverage(s["bench"].get("INDIAVIX"))
    return {"Sectors": sector, "MidSmallcap 400": mid, "India VIX": vix}


def dashboard(db, note: str = ""):
    s = status_snapshot(db)
    p = percentages(s)
    overall = sum(p.values()) / 3
    print("\n" + "=" * 62)
    print(" PMS PHASE-1 LOCAL DATA BACKFILL")
    print("=" * 62)
    print(f"Measured coverage  {bar(overall)} (sectors + benchmark sessions only)")
    print(f"1. Sectors       {bar(p['Sectors'])}  {s['sector']}/{s['total']} stocks")
    mid = s['bench'].get('NIFTYMIDSMALLCAP400', {})
    vix = s['bench'].get('INDIAVIX', {})
    print(f"2. MidSmall 400  {bar(p['MidSmallcap 400'])}  {mid.get('rows',0)} rows")
    print(f"3. India VIX     {bar(p['India VIX'])}  {vix.get('rows',0)} rows")
    print(f"4. Pre-2024 GSM  {s['gsm']} intervals loaded; completeness not inferred from row count")
    for code, item in (("MidSmall 400", mid), ("India VIX", vix)):
        print(f"  {code}: {item.get('missing_sessions', 0)} missing sessions; latest {item.get('max_date')}")
    print(f"Critical DQ errors still open: {s['errors']}")
    if note:
        print(f"\n{note}")
    print("=" * 62)
    return s, p


def resolve_loaded_surveillance_flag(db) -> list[int]:
    """Resolve only the old NOT-LOADED flag once its specific condition is false.

    This does not certify full historical coverage or resolve unrelated errors.
    """
    with db.begin() as conn:
        return list(conn.execute(text("""
            update pms.data_quality_issues q
            set resolved_at=now(), message=q.message ||
                ' Resolved after verifying official ASM/GSM legacy and recent open snapshots, with no inverted intervals. Full coverage audit remains separate.'
            where dataset='surveillance' and code='ASM_GSM_HISTORY_MISSING'
              and severity='ERROR' and resolved_at is null
              and not exists (select 1 from pms.surveillance_history where effective_to < effective_from)
              and not exists (
                select 1 from (values ('ASM'),('GSM')) as types(kind)
                where not exists (
                    select 1 from pms.surveillance_history h where h.surveillance_type=types.kind
                    and h.source like 'NSE_%' and h.effective_from < date '2024-01-23'
                ) or not exists (
                    select 1 from pms.surveillance_history h where h.surveillance_type=types.kind
                    and h.source='NSE_SURVEILLANCE' and h.effective_from >= current_date-10
                    and h.effective_to is null
                )
              )
            returning id
        """)).scalars())


def audit_failures(final: dict) -> list[str]:
    failures = []
    if final["sector"] < final["total"]:
        failures.append(f"sector coverage {final['sector']}/{final['total']}")
    for code in ("NIFTYMIDSMALLCAP400", "INDIAVIX"):
        item = final["bench"].get(code, {})
        if item.get("calendar_unavailable", True):
            failures.append(f"{code} session calendar unavailable")
        if item.get("stale", True):
            failures.append(f"{code} missing/stale")
        if item.get("missing_sessions", 0):
            failures.append(f"{code}: {item['missing_sessions']} missing sessions; "
                            f"sample {item['missing_dates'][:12]}")
    if final["gsm"] == 0:
        failures.append("pre-2024 GSM produced no intervals")
    if final["errors"]:
        failures.append(f"{final['errors']} unresolved ERROR data-quality issue(s)")
    return failures


def print_open_errors(db):
    with db.connect() as conn:
        issues = conn.execute(text("""
            select id,dataset,instrument_id,code,message from pms.data_quality_issues
            where severity='ERROR' and resolved_at is null order by id
        """)).mappings().all()
    for issue in issues:
        print("  DQ:", json.dumps(dict(issue), default=str))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-legacy-gsm", action="store_true",
                        help="Reuse previously loaded GSM intervals; final checks still run")
    parser.add_argument("--audit-only", action="store_true", help="Read-only audit; do not fetch or resolve flags")
    args = parser.parse_args()
    db = engine()
    with db.connect() as conn:
        conn.execute(text("select 1"))
    if args.audit_only:
        final, _ = dashboard(db, "Read-only Phase-1 audit")
        failures = audit_failures(final)
        print_open_errors(db)
        for failure in failures:
            print(" -", failure)
        print("PASS: implemented data gates passed; full GSM history review remains separate." if not failures else "NOT COMPLETE")
        raise SystemExit(2 if failures else 0)
    dashboard(db, "Database connection OK. Starting remaining Phase-1 jobs...")

    job_errors: list[str] = []

    print("\n[1/4] Sector classifications")
    try:
        from scripts.ingest_sector_classifications import ingest as ingest_sectors

        def sector_progress(n, total, symbol):
            pct = 100 * n / max(1, total)
            print(f"\r[1/4] Fetching NSE sectors {bar(pct)}  {n}/{total}  {symbol:16}", end="", flush=True)

        sector_result = ingest_sectors(sector_progress)
        print("\nSector result:", sector_result)
    except Exception as exc:
        msg = f"Sector job failed: {type(exc).__name__}: {exc}"
        job_errors.append(msg)
        print("\n", msg)
    dashboard(db, "Sector pass finished. Continuing to benchmarks...")

    print("\n[2/4 + 3/4] Official Nifty MidSmallcap 400 + India VIX history")
    print("Fetching official history in monthly chunks with trading-date coverage checks.")
    try:
        import scripts.ingest_module_e_benchmarks as benchmark
        benchmark_result = benchmark.ingest(date(2018, 1, 1), date.today())
        print("Benchmark result:", benchmark_result)
    except Exception as exc:
        msg = f"Benchmark/VIX job failed: {type(exc).__name__}: {exc}"
        job_errors.append(msg)
        print(msg)
    dashboard(db, "Benchmark/VIX pass finished. Continuing to legacy GSM...")

    if args.skip_legacy_gsm:
        print("\n[4/4] Reusing existing GSM intervals (--skip-legacy-gsm); final checks still apply.")
    else:
        print("\n[4/4] Pre-2024 GSM reconstruction from NSE circular archive")
        try:
            from scripts.ingest_legacy_gsm import ingest as ingest_gsm

            def gsm_progress(n, total, window):
                pct = 100 * n / max(1, total)
                print(f"\r[4/4] Reading GSM circulars {bar(pct)}  {n}/{total}  {window:28}", end="", flush=True)

            gsm_result = ingest_gsm(gsm_progress)
            print("\nGSM result:", gsm_result)
        except Exception as exc:
            msg = f"Legacy GSM job failed: {type(exc).__name__}: {exc}"
            job_errors.append(msg)
            print("\n", msg)


    resolved = resolve_loaded_surveillance_flag(db)
    if resolved:
        print("Resolved verified stale surveillance-not-loaded flags:", resolved)

    final, _ = dashboard(db, "All available fetchers have finished. Running final audit...")

    failures = audit_failures(final)
    failures.extend(job_errors)
    print_open_errors(db)

    print("\nFINAL AUDIT")
    if job_errors:
        print("Source/job errors encountered:")
        for item in job_errors:
            print(" -", item)
    if failures:
        print("NOT 100% YET. Remaining items:")
        for item in failures:
            print(" -", item)
        print("\nThis command is resumable. Fix/retry any failed source and run it again; existing rows are preserved.")
        raise SystemExit(2)
    print("PASS: implemented Phase-1 data gates passed. Full GSM history review remains separate.")


if __name__ == "__main__":
    main()
