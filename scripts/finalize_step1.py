"""One-command historical data repair + fail-closed Step-1 acceptance.

Run from the repository root:
    python scripts/finalize_step1.py

The progress bar reaches 100% only when the final historical integrity audit has
zero pending repairs. Existing source-backed rows are preserved; the command is
safe to rerun after interruption.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
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


def completed_cutoff() -> date:
    """Use yesterday in IST so a partially completed current session is never certified."""
    return datetime.now(IST).date() - timedelta(days=1)


def run(cmd: list[str], label: str) -> None:
    print(f"\n\n> {label}\n  {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


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
    return engine


def repair_reference_history(engine) -> dict:
    from packages.data_layer.reference_gap_ingestion import ReferenceGapIngestor

    ing = ReferenceGapIngestor(engine)
    return {
        "corporate_actions": ing.ingest_corporate_actions(),
        "shareholding": ing.ingest_shareholding(),
        "index_membership": ing.ingest_index_membership_history(),
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

        # 257-session warmup for the 2018 historical audit plus all Module-E inputs.
        run([
            sys.executable, "scripts/ingest_module_e_benchmarks.py",
            "--start", "2016-11-01", "--end", args.end.isoformat(),
        ], "official benchmark/calendar repair")
        p.step(15, "official calendar + benchmarks repaired")

        # Price repair is intentionally chunked by calendar year so a rerun is resumable
        # and the user sees useful progress rather than one opaque long job.
        warmup_start = date(2016, 11, 1)
        years = list(range(warmup_start.year, args.end.year + 1))
        for idx, year in enumerate(years, 1):
            start = warmup_start if year == warmup_start.year else date(year, 1, 1)
            end = min(args.end, date(year, 12, 31))
            output = ARTIFACT_ROOT / "step1" / f"prices-{year}.json"
            run([
                sys.executable, "scripts/backfill_official_historical_prices.py",
                "--start", start.isoformat(), "--end", end.isoformat(),
                "--output", str(output),
            ], f"official NSE prices {year}")
            p.step(15 + round(35 * idx / max(1, len(years))), f"official NSE prices through {year}")

        # Current source-backed classifications are refreshed, but never copied backwards.
        run([sys.executable, "scripts/ingest_sector_classifications.py"], "NSE sector classification refresh")
        p.step(55, "sector classifications refreshed")

        # Reconstruct legacy GSM from the actual NSE archive. Existing source-backed ASM
        # rows are preserved and verified below; no current snapshot is backdated.
        run([sys.executable, "scripts/ingest_legacy_gsm.py"], "legacy NSE GSM archive reconstruction")
        p.step(62, "legacy surveillance archive refreshed")

        reference = repair_reference_history(engine)
        ref_path = ARTIFACT_ROOT / "step1" / "reference-repair.json"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(json.dumps(reference, indent=2, default=str) + "\n")
        p.step(70, "shareholding/actions/index reference history repaired")

        # Financial discovery is sharded for source pacing but runs sequentially locally.
        for shard in range(8):
            output = ARTIFACT_ROOT / "step1" / f"financials-{shard}.json"
            run([
                sys.executable, "scripts/backfill_official_historical_financials.py",
                "--start", args.start.isoformat(), "--end", args.end.isoformat(),
                "--shard", str(shard), "--shards", "8", "--output", str(output),
            ], f"official NSE financial history shard {shard + 1}/8")
            p.step(70 + round(15 * (shard + 1) / 8), f"financial history {shard + 1}/8")

        evidence = evidence_snapshot(engine, args.matrix_start, args.matrix_end)
        if not evidence["legacy_surveillance_source_backed"]:
            raise RuntimeError("source-backed pre-2024 ASM/GSM evidence is incomplete")
        if int(evidence["historical_sector_window"].get("rows") or 0) == 0:
            raise RuntimeError(
                "no dated sector_history overlaps 2023-2024; current classifications were NOT backdated"
            )
        p.step(90, "price/sector/surveillance evidence gates passed")

        timestamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        audit = ARTIFACT_ROOT / "step1" / f"audit-{timestamp}.json"
        run([
            sys.executable, "scripts/run_data_integrity_audit.py", "--run",
            "--start", args.start.isoformat(), "--end", args.end.isoformat(),
            "--report", str(audit),
        ], "full historical integrity audit")
        p.step(97, "full integrity audit completed")

        report = json.loads(audit.read_text(encoding="utf-8"))
        repair_path = audit.with_suffix(".repair.json")
        repair = json.loads(repair_path.read_text(encoding="utf-8"))
        pending = int(repair.get("pending_tasks", -1))
        audit_sha = hashlib.sha256(audit.read_bytes()).hexdigest()
        complete = repair.get("status") == "READY_FOR_REPLAY" and pending == 0
        payload = {
            "version": "PMS_STEP1_FINAL_v1",
            "created_at": datetime.now(IST).isoformat(),
            "status": "STEP1_COMPLETE" if complete else "STEP1_BLOCKED",
            "scope": {"start": args.start.isoformat(), "end": args.end.isoformat()},
            "matrix_scope": {"start": args.matrix_start.isoformat(), "end": args.matrix_end.isoformat()},
            "audit_report": str(audit.relative_to(ROOT)),
            "audit_sha256": audit_sha,
            "audit_run_id": report.get("run_id"),
            "audit_summary": report.get("summary"),
            "repair_status": repair.get("status"),
            "pending_repairs": pending,
            "pending_tasks": repair.get("tasks", []),
            "evidence": evidence,
        }
        write_certificate(payload)
        if not complete:
            p.fail(f"Step 1 still has {pending} audited repair/review item(s). See {CERTIFICATE.relative_to(ROOT)}")
            return 2

        p.step(100, "STEP 1 COMPLETE — historical dataset READY_FOR_REPLAY")
        print(f"Certificate: {CERTIFICATE.relative_to(ROOT)}")
        return 0
    except KeyboardInterrupt:
        p.fail("interrupted; rerun the same command to resume")
        return 130
    except Exception as exc:
        p.fail(f"{type(exc).__name__}: {exc}")
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
