from __future__ import annotations

"""Resilient benchmark/calendar repair used by the two-command local finalizer.

Design goals:
- Fresh NSE session per retry so one timed-out HTTP session cannot poison the run.
- NIFTY50 is refreshed for every month because it is the calendar denominator.
- Other benchmark windows are skipped when their official rows already cover every
  NIFTY50 session for that month.
- Exponential backoff on transient source failures.
- Final fail-closed equality check for all four required series.
"""

import argparse
import json
import time
from datetime import date
from pathlib import Path

from nse import NSE
from sqlalchemy import text

from scripts import ingest_module_e_benchmarks as base

REQUIRED = ("NIFTY50", "NIFTY500", "NIFTYMIDSMALLCAP400", "INDIAVIX")


def _dates(engine, code: str, start: date, end: date) -> set[date]:
    with engine.connect() as conn:
        return set(conn.execute(text("""
            select distinct trade_date
            from pms.module_e_benchmark_bars
            where benchmark_code=:code
              and source in ('NSE_HISTORICAL_OR','NSE_DAILY_INDEX')
              and trade_date between :start and :end
        """), {"code": code, "start": start, "end": end}).scalars())


def _fetch_with_retry(cache: Path, code: str, start: date, end: date, attempts: int = 5):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            # A new client/session per attempt is intentional: urllib/requests
            # sessions can remain unhealthy after a socket read timeout.
            with NSE(download_folder=cache) as nse:
                return base.fetch_window(nse, code, start, end)
        except Exception as exc:
            last = exc
            if attempt == attempts:
                break
            delay = min(20, 2 ** (attempt - 1))
            print(
                f"  RETRY {code} {start}..{end}: attempt {attempt}/{attempts} "
                f"after {type(exc).__name__}; sleeping {delay}s",
                flush=True,
            )
            time.sleep(delay)
    assert last is not None
    raise last


def ingest(start: date, end: date) -> dict:
    engine = base._engine()
    cache = base.ROOT / ".cache_nse"
    cache.mkdir(exist_ok=True)
    windows = list(base.month_windows(start, end))
    counts = {"calendar": 0, "nifty500": 0, "index": 0, "vix": 0, "skipped_complete": 0}
    diagnostics: list[dict] = []
    try:
        for n, (first, last) in enumerate(windows, 1):
            # Always refresh the official NIFTY50 month first. It defines the
            # session set used to judge completeness of the other series.
            rows, detail = _fetch_with_retry(cache, "NIFTY50", first, last)
            counts["calendar"] += base.upsert_rows(engine, rows)
            diagnostics.append(detail)
            expected = {r["trade_date"] for r in rows}
            if not expected:
                raise RuntimeError(f"NIFTY50 calendar empty for {first}..{last}")
            print(f"  [{n}/{len(windows)}] NIFTY50 {first}..{last}: {len(rows)} dates", flush=True)

            for code, label in (
                ("NIFTY500", "nifty500"),
                ("NIFTYMIDSMALLCAP400", "index"),
                ("INDIAVIX", "vix"),
            ):
                existing = _dates(engine, code, first, last)
                if expected.issubset(existing):
                    counts["skipped_complete"] += 1
                    print(
                        f"  [{n}/{len(windows)}] {code} {first}..{last}: "
                        f"already complete ({len(expected)} dates)",
                        flush=True,
                    )
                    continue
                rows, detail = _fetch_with_retry(cache, code, first, last)
                counts[label] += base.upsert_rows(engine, rows)
                diagnostics.append(detail)
                observed = _dates(engine, code, first, last)
                missing = sorted(expected - observed)
                if missing:
                    raise RuntimeError(
                        f"{code} still missing {len(missing)} official sessions in "
                        f"{first}..{last}: {[str(d) for d in missing[:10]]}"
                    )
                print(f"  [{n}/{len(windows)}] {code} {first}..{last}: {len(rows)} dates", flush=True)

        # Final all-series equality check. The older coverage_audit helper only
        # reports two legacy feeds, so the finalizer deliberately checks all 4.
        expected = _dates(engine, "NIFTY50", start, end)
        if not expected:
            raise RuntimeError("official NIFTY50 session calendar is empty")
        coverage = {}
        for code in REQUIRED:
            observed = _dates(engine, code, start, end)
            missing = sorted(expected - observed)
            unexpected = sorted(observed - expected)
            coverage[code] = {
                "rows": len(observed),
                "expected_sessions": len(expected),
                "missing_sessions": len(missing),
                "unexpected_sessions": len(unexpected),
                "missing_dates": [str(d) for d in missing[:50]],
                "unexpected_dates": [str(d) for d in unexpected[:50]],
            }
            if missing:
                raise RuntimeError(
                    f"{code} missing {len(missing)} NIFTY50 sessions after repair"
                )
        return {"written": counts, "coverage": coverage, "windows_fetched": diagnostics}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must be on or after --start")
    print(json.dumps(ingest(args.start, args.end), indent=2, default=str))


if __name__ == "__main__":
    main()
