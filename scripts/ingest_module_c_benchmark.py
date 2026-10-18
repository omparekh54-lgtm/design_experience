"""Import official Nifty 500 history required by Module C relative strength."""

from __future__ import annotations
import argparse, json, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from nse import NSE
from sqlalchemy import text
from scripts.ingest_module_e_benchmarks import (
    _engine,
    fetch_window,
    month_windows,
    upsert_rows,
    SOURCE,
)


def ingest(start=None, end=None):
    end = end or date.today()
    start = start or end - timedelta(days=370)
    engine = _engine()
    written = 0
    failures = []
    cache = ROOT / ".cache_nse"
    cache.mkdir(exist_ok=True)
    with NSE(download_folder=cache) as nse:
        for first, last in month_windows(start, end):
            try:
                rows, detail = fetch_window(nse, "NIFTY500", first, last)
                written += upsert_rows(engine, rows)
                print(json.dumps(detail), flush=True)
            except Exception as exc:
                failures.append(f"{first}..{last}: {exc}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("""select benchmark_code,trade_date from pms.module_e_benchmark_bars
            where benchmark_code in ('NIFTY500','NIFTY50') and source=:source
            and trade_date between :start and :end"""),
            {"source": SOURCE, "start": start, "end": end},
        ).all()
    expected = {d for code, d in rows if code == "NIFTY50"}
    actual = {d for code, d in rows if code == "NIFTY500"}
    result = {
        "written": written,
        "expected_sessions": len(expected),
        "actual_sessions": len(actual),
        "missing_dates": [str(d) for d in sorted(expected - actual)],
        "unexpected_dates": [str(d) for d in sorted(actual - expected)],
        "latest": str(max(actual)) if actual else None,
        "failed_windows": failures,
    }
    calendar_ok = bool(expected) and (end - max(expected)).days <= 7
    for first, last in month_windows(start, end):
        if (last - first).days >= 7 and not any(first <= d <= last for d in expected):
            calendar_ok = False
    result["calendar_available"] = calendar_ok
    if failures or not calendar_ok or expected != actual:
        raise RuntimeError(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(ingest(args.start, args.end), indent=2))


if __name__ == "__main__":
    main()
