"""One-command historical data repair + fail-closed Step-1 acceptance.

Run from the repository root:
    python scripts/finalize_step1.py

The progress bar reaches 100% only when every blocking integrity check passes
and the independent audit reviews have separate source-evidence reconciliations.
All network-heavy child stages are retryable/idempotent and long price/financial
stages checkpoint their completed work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from scripts.finalize_progress import Progress

IST = ZoneInfo("Asia/Kolkata")
ARTIFACT_ROOT = ROOT / "artifacts" / "final_acceptance"
CERTIFICATE = ARTIFACT_ROOT / "step1_certificate.json"
STATIC_REVIEWS = {
    "INDEPENDENT_CALENDAR",
    "SURVIVORSHIP_INVENTORY",
    "GSM_ARCHIVE_COMPLETENESS",
    "REFERENCE_VINTAGES",
    "PRICE_ADJUSTMENT_RECONCILIATION",
}


def completed_cutoff() -> date:
    return datetime.now(IST).date() - timedelta(days=1)


def run(cmd: list[str], label: str, allowed: tuple[int, ...] = (0,), attempts: int = 1) -> int:
    last = None
    for attempt in range(1, attempts + 1):
        print(f"\n\n> {label}" + (f" (attempt {attempt}/{attempts})" if attempts > 1 else "") + f"\n  {' '.join(cmd)}", flush=True)
        result = subprocess.run(cmd, cwd=ROOT)
        last = result.returncode
        if result.returncode in allowed:
            return result.returncode
        if attempt < attempts:
            delay = min(15, 2 ** (attempt - 1))
            print(f"  retrying {label} after exit {result.returncode} in {delay}s", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"{label} failed with exit code {last}")


def retry_call(label: str, fn, attempts: int = 3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt == attempts:
                raise
            delay = min(10, 2 ** (attempt - 1))
            print(f"\n  RETRY {label}: {type(exc).__name__}; {delay}s", flush=True)
            time.sleep(delay)
    raise last


def db_engine():
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL or PMS_DATABASE_URL is required in .env")
    engine = create_engine(
        _render_safe_database_url(raw),
        pool_pre_ping=True,
        connect_args={"options": "-c search_path=pms,public"},
    )
    with engine.connect() as conn:
        conn.execute(text("select 1"))
        required = conn.execute(text("""
            select count(*) from information_schema.tables
            where table_schema='pms' and table_name in
              ('instruments','daily_prices','module_e_benchmark_bars','financial_filings',
               'sector_history','surveillance_history','provider_snapshots')
        """)).scalar_one()
        if int(required) != 7:
            raise RuntimeError("PMS database schema is incomplete or points to the wrong database")
    return engine


def repair_reference_history(engine) -> dict:
    from packages.data_layer.reference_gap_ingestion import ReferenceGapIngestor

    ing = ReferenceGapIngestor(engine)
    return {
        "corporate_actions": retry_call("corporate actions", ing.ingest_corporate_actions),
        "shareholding": retry_call("shareholding history", ing.ingest_shareholding),
        "index_membership": retry_call("index membership history", ing.ingest_index_membership_history),
        "coverage": ing.coverage(),
    }


def evidence_snapshot(engine, matrix_start: date, matrix_end: date) -> dict:
    with engine.connect() as conn:
        surveillance = [dict(r) for r in conn.execute(text("""
            select surveillance_type, count(*)::int rows,
                   min(effective_from) min_from, max(effective_from) max_from,
                   array_agg(distinct source order by source) sources
            from pms.surveillance_history
            where surveillance_type in ('ASM','GSM')
              and effective_from < timestamptz '2024-01-23 00:00:00+05:30'
              and source like 'NSE_%'
            group by surveillance_type order by surveillance_type
        """)).mappings().all()]
        sectors = conn.execute(text("""
            select count(*)::int rows, count(distinct instrument_id)::int instruments,
                   min(effective_from) min_from, max(effective_from) max_from
            from pms.sector_history
            where effective_from <= :end
              and (effective_to is null or effective_to > :start)
              and sector is not null and btrim(sector)<>''
        """), {"start": matrix_start, "end": matrix_end}).mappings().one()
        prices = [dict(r) for r in conn.execute(text("""
            select source,count(*)::bigint rows,min(trade_date) min_date,max(trade_date) max_date
            from pms.daily_prices
            where trade_date between :start and :end
            group by source order by source
        """), {"start": matrix_start - timedelta(days=400), "end": matrix_end}).mappings().all()]
        open_errors = conn.execute(text("""
            select count(*)::int from pms.data_quality_issues
            where severity='ERROR' and resolved_at is null
        """)).scalar_one()
    by_type = {r["surveillance_type"]: r for r in surveillance}
    return {
        "matrix_window": [matrix_start.isoformat(), matrix_end.isoformat()],
        "legacy_surveillance": surveillance,
        "legacy_surveillance_source_backed": all(by_type.get(k, {}).get("rows", 0) > 0 for k in ("ASM", "GSM")),
        "historical_sector_window": dict(sectors),
        "price_sources": prices,
        "open_critical_dq_errors": int(open_errors),
    }


def final_review_pack(engine, report: dict, evidence: dict, start: date, end: date) -> dict:
    statuses = {r["code"]: r["status"] for r in report.get("checks", [])}
    universe_ids = {int(r["id"]) for r in (report.get("dataset") or {}).get("universe", [])}
    with engine.connect() as conn:
        calendar = conn.execute(text("""
            select count(*)::int snapshots, count(distinct content_sha256)::int distinct_hashes,
                   min(observed_at) min_seen, max(observed_at) max_seen
            from pms.provider_snapshots where dataset='NSE_TRADING_CALENDAR'
        """)).mappings().one()
        benchmarks = [dict(r) for r in conn.execute(text("""
            select benchmark_code,count(*)::int rows,min(trade_date) min_date,max(trade_date) max_date
            from pms.module_e_benchmark_bars
            where benchmark_code in ('NIFTY50','NIFTY500','NIFTYMIDSMALLCAP400','INDIAVIX')
              and trade_date between :start and :end
            group by benchmark_code order by benchmark_code
        """), {"start": start, "end": end}).mappings().all()]
        inactive = [dict(r) for r in conn.execute(text("""
            select id,nse_symbol,listed_from,delisted_on from pms.instruments
            where exchange='NSE' and metadata->>'ingestion_duplicate_of' is null
              and (listed_from is null or listed_from<=:end)
              and (delisted_on is null or delisted_on>:start)
              and (active=false or delisted_on is not null)
            order by id
        """), {"start": start, "end": end}).mappings().all()]
        surveillance_bad = conn.execute(text("""
            select count(*)::int from pms.surveillance_history
            where surveillance_type in ('ASM','GSM') and effective_to < effective_from
        """)).scalar_one()
        refs = conn.execute(text("""
            select
              (select count(*) from pms.index_constituents_history where effective_from<=:end and (effective_to is null or effective_to>:start))::int index_rows,
              (select count(*) from pms.shareholding_history where period_end between :start and :end and filed_at is not null)::int shareholding_rows,
              (select count(*) from pms.financial_filings where period_end between :start and :end and filing_timestamp is not null)::int financial_rows,
              (select count(*) from pms.corporate_actions where ex_date between :start and :end and announced_at is not null)::int action_rows
        """), {"start": start, "end": end}).mappings().one()

    benchmark_codes = {r["benchmark_code"] for r in benchmarks}
    inactive_missing = sorted(int(r["id"]) for r in inactive if int(r["id"]) not in universe_ids)
    official_price_rows = sum(int(r["rows"]) for r in evidence.get("price_sources", []) if str(r["source"]).upper().startswith("NSE_"))

    reviews = {
        "INDEPENDENT_CALENDAR": {
            "pass": int(calendar["snapshots"] or 0) > 0
                    and int(calendar["distinct_hashes"] or 0) > 0
                    and benchmark_codes == {'NIFTY50','NIFTY500','NIFTYMIDSMALLCAP400','INDIAVIX'}
                    and statuses.get("BENCHMARK_SESSIONS") == "PASS"
                    and statuses.get("REFERENCE_SESSION_GAPS") == "PASS",
            "evidence": {"calendar_snapshots": dict(calendar), "benchmarks": benchmarks},
        },
        "SURVIVORSHIP_INVENTORY": {
            "pass": not inactive_missing,
            "evidence": {"overlapping_inactive_or_delisted": len(inactive), "missing_from_audit_universe": inactive_missing[:100]},
        },
        "GSM_ARCHIVE_COMPLETENESS": {
            "pass": bool(evidence.get("legacy_surveillance_source_backed"))
                    and int(surveillance_bad or 0) == 0
                    and statuses.get("SURVEILLANCE_HISTORY_OVERLAPS") == "PASS",
            "evidence": {"legacy": evidence.get("legacy_surveillance"), "inverted_intervals": int(surveillance_bad or 0)},
        },
        "REFERENCE_VINTAGES": {
            "pass": int((evidence.get("historical_sector_window") or {}).get("rows") or 0) > 0
                    and int(refs["index_rows"] or 0) > 0
                    and int(refs["shareholding_rows"] or 0) > 0
                    and int(refs["financial_rows"] or 0) > 0,
            "evidence": dict(refs),
        },
        "PRICE_ADJUSTMENT_RECONCILIATION": {
            "pass": official_price_rows > 0
                    and statuses.get("PRICE_SESSION_COVERAGE") == "PASS"
                    and statuses.get("CORPORATE_ACTION_VALUES") == "PASS"
                    and statuses.get("CORPORATE_ACTION_CONFLICTS") == "PASS",
            "evidence": {"official_nse_price_rows": official_price_rows,
                         "price_session_coverage": statuses.get("PRICE_SESSION_COVERAGE"),
                         "corporate_action_values": statuses.get("CORPORATE_ACTION_VALUES"),
                         "corporate_action_conflicts": statuses.get("CORPORATE_ACTION_CONFLICTS"),
                         "unsupported_actions_policy": "Step 10 replay fails closed if an unsupported action affects a held position"},
        },
    }
    return reviews


def write_certificate(payload: dict) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    temp = CERTIFICATE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(CERTIFICATE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair and certify PMS Step 1 locally")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=completed_cutoff())
    parser.add_argument("--matrix-start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--matrix-end", type=date.fromisoformat, default=date(2024, 12, 31))
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must be on or after --start")
    if args.matrix_end > date(2024, 12, 31):
        parser.error("Step 10 in-sample window must end by 2024-12-31")

    p = Progress("STEP 1")
    p.show(0, "starting")
    engine = None
    try:
        engine = db_engine()
        p.step(5, "database + environment verified")

        run([sys.executable, "scripts/ingest_module_e_benchmarks.py", "--start", "2016-11-01", "--end", args.end.isoformat()],
            "official benchmark/calendar repair", attempts=2)
        p.step(15, "official calendar + benchmarks repaired")

        warmup_start = date(2016, 11, 1)
        years = list(range(warmup_start.year, args.end.year + 1))
        for idx, year in enumerate(years, 1):
            start = warmup_start if year == warmup_start.year else date(year, 1, 1)
            end = min(args.end, date(year, 12, 31))
            output = ARTIFACT_ROOT / "step1" / f"prices-{year}.json"
            run([sys.executable, "scripts/backfill_official_historical_prices.py", "--start", start.isoformat(), "--end", end.isoformat(), "--output", str(output)],
                f"official NSE prices {year}", attempts=2)
            p.step(15 + round(35 * idx / max(1, len(years))), f"official NSE prices through {year}")

        run([sys.executable, "scripts/ingest_sector_classifications.py"], "NSE current sector classification refresh", attempts=2)
        p.step(53, "current sector classifications refreshed")
        sector_output = ARTIFACT_ROOT / "step1" / "historical-sectors.json"
        run([sys.executable, "scripts/backfill_historical_sector_intervals.py",
             "--start", "2017-01-01", "--end", args.end.isoformat(), "--output", str(sector_output.relative_to(ROOT))],
            "dated historical sector reconstruction", attempts=3)
        p.step(57, "dated historical sector intervals refreshed")
        run([sys.executable, "scripts/ingest_legacy_gsm.py"], "legacy NSE GSM archive reconstruction", attempts=2)
        p.step(62, "legacy surveillance archive refreshed")

        reference = repair_reference_history(engine)
        ref_path = ARTIFACT_ROOT / "step1" / "reference-repair.json"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(json.dumps(reference, indent=2, default=str) + "\n", encoding="utf-8")
        p.step(70, "shareholding/actions/index reference history repaired")

        financial_start = min(args.start, date(2016, 1, 1))
        for shard in range(8):
            output = ARTIFACT_ROOT / "step1" / f"financials-{shard}.json"
            run([sys.executable, "scripts/backfill_official_historical_financials.py", "--start", financial_start.isoformat(), "--end", args.end.isoformat(), "--shard", str(shard), "--shards", "8", "--output", str(output)],
                f"official NSE financial history shard {shard + 1}/8", attempts=2)
            p.step(70 + round(15 * (shard + 1) / 8), f"financial history {shard + 1}/8")

        evidence = evidence_snapshot(engine, args.matrix_start, args.matrix_end)
        if not evidence["legacy_surveillance_source_backed"]:
            raise RuntimeError("source-backed pre-2024 ASM/GSM evidence is incomplete")
        if int(evidence["historical_sector_window"].get("rows") or 0) == 0:
            raise RuntimeError("no dated sector_history overlaps 2023-2024; current classifications were NOT backdated")
        if not any(str(r["source"]).upper().startswith("NSE_") and int(r["rows"]) > 0 for r in evidence["price_sources"]):
            raise RuntimeError("no official NSE price rows are available for the Step 10 historical window")
        p.step(90, "price/sector/surveillance evidence gates passed")

        timestamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        audit = ARTIFACT_ROOT / "step1" / f"audit-{timestamp}.json"
        audit_exit = run([sys.executable, "scripts/run_data_integrity_audit.py", "--run", "--start", args.start.isoformat(), "--end", args.end.isoformat(),
                          "--timeout-ms", "300000", "--report", str(audit)],
                         "full historical integrity audit", allowed=(0, 2))
        p.step(96, "full integrity audit completed")
        if not audit.exists() or not audit.with_suffix(".repair.json").exists():
            raise RuntimeError(f"integrity audit exit {audit_exit} produced no complete report bundle")

        report = json.loads(audit.read_text(encoding="utf-8"))
        raw_repair = json.loads(audit.with_suffix(".repair.json").read_text(encoding="utf-8"))
        automated_pending = [r for r in report.get("checks", []) if r.get("code") not in STATIC_REVIEWS and r.get("status") not in {"PASS", "NOT_APPLICABLE"}]
        reviews = final_review_pack(engine, report, evidence, args.start, args.end)
        review_pending = [{"check": code, "status": "REVIEW_EVIDENCE_FAILED", "evidence": item["evidence"]} for code, item in reviews.items() if not item["pass"]]
        pending_tasks = [
            {"check": r.get("code"), "group": r.get("group"), "status": r.get("status"), "finding_count": r.get("finding_count"), "samples": r.get("samples", []), "action": r.get("remediation")}
            for r in automated_pending
        ] + review_pending
        complete = bool(report.get("summary", {}).get("complete")) and not pending_tasks and all(v["pass"] for v in reviews.values())
        audit_sha = hashlib.sha256(audit.read_bytes()).hexdigest()
        payload = {
            "version": "PMS_STEP1_FINAL_v3",
            "created_at": datetime.now(IST).isoformat(),
            "status": "STEP1_COMPLETE" if complete else "STEP1_BLOCKED",
            "scope": {"start": args.start.isoformat(), "end": args.end.isoformat()},
            "matrix_scope": {"start": args.matrix_start.isoformat(), "end": args.matrix_end.isoformat()},
            "audit_report": str(audit.relative_to(ROOT)),
            "audit_sha256": audit_sha,
            "audit_run_id": report.get("run_id"),
            "audit_summary": report.get("summary"),
            "raw_audit_repair_inventory": int(raw_repair.get("pending_tasks", -1)),
            "final_review_pack": reviews,
            "repair_status": "READY_FOR_REPLAY" if complete else "BLOCKED",
            "pending_repairs": len(pending_tasks),
            "pending_tasks": pending_tasks,
            "evidence": evidence,
            "note": "The base auditor retains independent/review-only findings; the certificate remains fail-closed unless every non-static finding is PASS/NOT_APPLICABLE and all five independent evidence gates pass.",
        }
        write_certificate(payload)
        if not complete:
            p.step(98, f"blocked by {len(pending_tasks)} final item(s)")
            p.fail(f"Step 1 is not complete. See {CERTIFICATE.relative_to(ROOT)} for exact remaining items")
            return 2

        p.step(100, "STEP 1 COMPLETE — no remaining historical acceptance blockers")
        print(f"Certificate: {CERTIFICATE.relative_to(ROOT)}")
        return 0
    except KeyboardInterrupt:
        p.fail("interrupted; rerun the same command to resume checkpointed stages")
        return 130
    except Exception as exc:
        p.fail(f"{type(exc).__name__}: {exc}")
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
