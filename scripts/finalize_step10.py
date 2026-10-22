"""One-command Step-10 matrix execution + fail-closed acceptance.

Run only after Step 1 reaches 100%:
    python scripts/finalize_step10.py

The command keeps 2025+ untouched as out-of-sample, runs the declared
2023-2024 matrix twice, verifies reproducibility, and writes a final certificate.
Interrupted runs resume from stable checkpoints only when Step-1 data, config,
and code fingerprints are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.main import _render_safe_database_url
from scripts.finalize_progress import Progress

IST = ZoneInfo("Asia/Kolkata")
ARTIFACT_ROOT = ROOT / "artifacts" / "final_acceptance"
STEP1_CERT = ARTIFACT_ROOT / "step1_certificate.json"
STEP10_CERT = ARTIFACT_ROOT / "step10_certificate.json"
BASE_CONFIG = ROOT / "config" / "strategy_v1.yaml"
STEP10_DIR = ARTIFACT_ROOT / "step10"
RUN1 = STEP10_DIR / "matrix-run1.json"
RUN2 = STEP10_DIR / "matrix-run2.json"
STATE = STEP10_DIR / "checkpoint-state.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


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


def require_step1(start: date, end: date) -> dict:
    if not STEP1_CERT.is_file():
        raise RuntimeError("Step 1 certificate is missing; run python scripts/finalize_step1.py first")
    cert = json.loads(STEP1_CERT.read_text(encoding="utf-8"))
    if cert.get("status") != "STEP1_COMPLETE" or int(cert.get("pending_repairs", -1)) != 0:
        raise RuntimeError("Step 1 is not fully certified yet")
    scope = cert.get("matrix_scope") or {}
    if scope.get("start") != start.isoformat() or scope.get("end") != end.isoformat():
        raise RuntimeError("Step 1 certificate matrix scope does not match this Step 10 run")
    evidence = cert.get("evidence") or {}
    if not evidence.get("legacy_surveillance_source_backed"):
        raise RuntimeError("Step 1 certificate does not prove source-backed legacy ASM/GSM")
    if int((evidence.get("historical_sector_window") or {}).get("rows") or 0) <= 0:
        raise RuntimeError("Step 1 certificate does not prove dated sector history")
    audit_rel = cert.get("audit_report")
    if not audit_rel:
        raise RuntimeError("Step 1 certificate has no audit report")
    audit = (ROOT / audit_rel).resolve()
    if not audit.is_relative_to(ROOT.resolve()) or not audit.is_file():
        raise RuntimeError("Step 1 audit report referenced by certificate is missing")
    if sha256(audit) != cert.get("audit_sha256"):
        raise RuntimeError("Step 1 audit report changed after certification")
    return cert


def research_config() -> Path:
    cfg = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("fundamental"), dict):
        raise RuntimeError("strategy_v1.yaml is missing the fundamental configuration")
    cfg["fundamental"]["legacy_surveillance_verified"] = True
    cfg.setdefault("backtest", {})["step1_certificate"] = sha256(STEP1_CERT)
    cfg["backtest"]["final_acceptance_scope"] = "2023-2024_IN_SAMPLE_ONLY"
    path = STEP10_DIR / "certified_research_config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def run_checked(cmd: list[str], label: str, *, stdout_path: Path | None = None) -> int:
    print(f"\n\n> {label}\n  {' '.join(cmd)}", flush=True)
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("w", encoding="utf-8") as out:
            result = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
        if result.returncode:
            print(stdout_path.read_text(encoding="utf-8", errors="replace")[-12000:])
    else:
        result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")
    return result.returncode


def _quarantine(path: Path, tag: str) -> None:
    if not path.exists():
        return
    destination = path.with_name(path.name + f".stale-{tag}")
    counter = 1
    while destination.exists():
        destination = path.with_name(path.name + f".stale-{tag}-{counter}")
        counter += 1
    path.replace(destination)


def prepare_checkpoints(config: Path) -> dict:
    from packages.pms.matrix_data import code_fingerprint

    identity = hashlib.sha256(json.dumps({
        "step1": sha256(STEP1_CERT),
        "config": sha256(config),
        "code": code_fingerprint(),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    old = None
    if STATE.is_file():
        try:
            old = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            old = None
    if old and old.get("identity") != identity:
        tag = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        for report in (RUN1, RUN2):
            _quarantine(report, tag)
            _quarantine(report.parent / (report.stem + "_cells"), tag)
    payload = {"identity": identity, "created_at": datetime.now(IST).isoformat(),
               "step1_sha256": sha256(STEP1_CERT), "config_sha256": sha256(config),
               "code_fingerprint": code_fingerprint()}
    write_json(STATE, payload)
    return payload


def matrix_subprocess(cmd: list[str], report: Path, progress: Progress, low: int, high: int, label: str) -> None:
    print(f"\n\n> {label}\n  {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=ROOT)
    last = -1
    while proc.poll() is None:
        if report.is_file():
            try:
                payload = json.loads(report.read_text(encoding="utf-8"))
                cells = payload.get("cells", [])
                total = len(cells)
                done = sum(c.get("status") in {"COMPLETED", "NO_TRADES", "BLOCKED", "FAILED"} for c in cells)
                if total:
                    value = low + round((high - low) * done / total)
                    if value != last:
                        progress.step(value, f"{label}: {done}/{total} cells")
                        last = value
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(1.0)
    code = proc.wait()
    if code:
        raise RuntimeError(f"{label} failed with exit code {code}; inspect {report}")
    progress.step(high, f"{label} complete")


def canonical_result(report: dict) -> dict:
    cells = []
    for row in report.get("cells", []):
        diagnostics = row.get("diagnostics") or {}
        cells.append({
            "key": row.get("key"),
            "status": row.get("status"),
            "blockers": sorted(row.get("blockers") or []),
            "metrics": row.get("metrics"),
            "trade_frequency_sufficient": row.get("trade_frequency_sufficient"),
            "evidence_sha256": diagnostics.get("evidence_sha256"),
        })
    return {
        "version": report.get("version"),
        "code_version": report.get("code_version"),
        "dataset_fingerprint": (report.get("dataset") or {}).get("fingerprint"),
        "config_hash": report.get("config_hash"),
        "design_scope": report.get("design_scope"),
        "holdout": report.get("holdout"),
        "cells": cells,
    }


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def survivorship_audit(engine, report: dict, start: date, end: date) -> dict:
    manifest_ids = {int(r["id"]) for r in (report.get("dataset") or {}).get("universe", [])}
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            select id,nse_symbol,active,listed_from,delisted_on
            from pms.instruments
            where exchange='NSE'
              and metadata->>'ingestion_duplicate_of' is null
              and (listed_from is null or listed_from<=:end)
              and (delisted_on is null or delisted_on>:start)
              and (active=false or delisted_on is not null)
            order by id
        """), {"start": start, "end": end}).mappings().all()]
    expected = {int(r["id"]) for r in rows}
    missing = sorted(expected - manifest_ids)
    return {
        "overlapping_inactive_or_delisted": len(expected),
        "included_in_matrix_universe": len(expected & manifest_ids),
        "missing_ids": missing[:100],
        "pass": not missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and certify PMS Step 10 locally")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    args = parser.parse_args()
    if args.end > date(2024, 12, 31):
        parser.error("2025 onward is reserved for out-of-sample testing and cannot be used here")
    if args.end < args.start:
        parser.error("--end must be on or after --start")

    p = Progress("STEP 10")
    p.show(0, "starting")
    engine = None
    try:
        step1 = require_step1(args.start, args.end)
        engine = db_engine()
        config = research_config()
        checkpoint_state = prepare_checkpoints(config)
        p.step(8, "Step 1 certificate + OOS boundary + checkpoint identity verified")

        tests = [
            "tests/test_backtest_matrix.py",
            "tests/test_matrix_data_api.py",
            "tests/test_matrix_replay_integration.py",
            "tests/test_unified_research_replay.py",
            "tests/test_historical_execution_consistency.py",
            "tests/test_historical_dataset.py",
        ]
        missing_tests = [t for t in tests if not (ROOT / t).is_file()]
        if missing_tests:
            raise RuntimeError(f"required regression tests missing: {missing_tests}")
        run_checked([sys.executable, "-m", "pytest", "-q", *tests], "matrix/leakage/replay regression suite")
        p.step(18, "leakage + replay regression suite passed")

        preflight = STEP10_DIR / "preflight.json"
        run_checked([
            sys.executable, "scripts/run_backtest_matrix.py", "--check",
            "--start", args.start.isoformat(), "--end", args.end.isoformat(),
            "--config", str(config),
        ], "all-cell historical data preflight", stdout_path=preflight)
        try:
            preflight_payload = json.loads(preflight.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("matrix preflight did not emit valid JSON") from exc
        if int(preflight_payload.get("ready_cells", 0)) != len(preflight_payload.get("cells", [])):
            raise RuntimeError("matrix preflight returned non-ready cells despite zero exit status")
        p.step(25, "all matrix cells are data-ready")

        base = [
            sys.executable, "scripts/run_backtest_matrix.py", "--run",
            "--start", args.start.isoformat(), "--end", args.end.isoformat(),
            "--config", str(config),
        ]
        matrix_subprocess(base + ["--report", str(RUN1)], RUN1, p, 25, 55, "matrix replay 1/2")
        matrix_subprocess(base + ["--report", str(RUN2), "--no-persist"], RUN2, p, 55, 85, "matrix replay 2/2")

        first = json.loads(RUN1.read_text(encoding="utf-8"))
        second = json.loads(RUN2.read_text(encoding="utf-8"))
        if (first.get("summary") or {}).get("status") != "COMPLETED":
            raise RuntimeError("first matrix replay did not complete every cell")
        if (second.get("summary") or {}).get("status") != "COMPLETED":
            raise RuntimeError("second matrix replay did not complete every cell")
        p.step(89, "both full matrix replays completed")

        survivor = survivorship_audit(engine, first, args.start, args.end)
        if not survivor["pass"]:
            raise RuntimeError("survivorship audit found overlapping inactive/delisted securities missing from matrix universe")
        p.step(92, "survivorship inclusion audit passed")

        canon1 = canonical_result(first)
        canon2 = canonical_result(second)
        fp1, fp2 = digest(canon1), digest(canon2)
        if fp1 != fp2:
            mismatch = STEP10_DIR / "reproducibility-mismatch.json"
            write_json(mismatch, {"run1": canon1, "run2": canon2, "fingerprints": [fp1, fp2]})
            raise RuntimeError(f"reproducibility mismatch; inspect {mismatch.relative_to(ROOT)}")
        if canon1.get("dataset_fingerprint") != canon2.get("dataset_fingerprint"):
            raise RuntimeError("dataset changed between replay runs")
        p.step(97, "reproducibility fingerprints match")

        bad = [c for c in first.get("cells", []) if c.get("status") not in {"COMPLETED", "NO_TRADES"}]
        if bad:
            raise RuntimeError(f"{len(bad)} matrix cells are not terminal-success states")
        certificate = {
            "version": "PMS_STEP10_FINAL_v2",
            "created_at": datetime.now(IST).isoformat(),
            "status": "STEP10_COMPLETE",
            "scope": {"start": args.start.isoformat(), "end": args.end.isoformat()},
            "oos_reservation": "2025 onward untouched",
            "step1_certificate_sha256": sha256(STEP1_CERT),
            "step1_audit_run_id": step1.get("audit_run_id"),
            "config": str(config.relative_to(ROOT)),
            "run1": str(RUN1.relative_to(ROOT)),
            "run2": str(RUN2.relative_to(ROOT)),
            "checkpoint_identity": checkpoint_state["identity"],
            "dataset_fingerprint": canon1.get("dataset_fingerprint"),
            "code_fingerprint": canon1.get("code_version"),
            "result_fingerprint": fp1,
            "reproducibility": "PASS",
            "survivorship": survivor,
            "missing_data_policy": "PASS_ALL_MATRIX_GATES",
            "leakage_regression_suite": "PASS",
            "all_cells_terminal_success": True,
            "cells": len(first.get("cells", [])),
        }
        write_json(STEP10_CERT, certificate)
        p.step(100, "STEP 10 COMPLETE — matrix accepted and reproducible")
        print(f"Certificate: {STEP10_CERT.relative_to(ROOT)}")
        print(f"Result fingerprint: {fp1}")
        return 0
    except KeyboardInterrupt:
        p.fail("interrupted; rerun the same command — stable matrix checkpoints will resume when identity is unchanged")
        return 130
    except Exception as exc:
        p.fail(f"{type(exc).__name__}: {exc}")
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
