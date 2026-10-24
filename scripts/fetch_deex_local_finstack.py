from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import sys

import pandas as pd
import finstack as fs

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def save_frame(root: Path, family: str, name: str, frame) -> int:
    target = root / "finstack" / family
    target.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:180]
    if frame is None:
        return 0
    if not isinstance(frame, pd.DataFrame):
        try:
            frame = pd.DataFrame(frame)
        except Exception:
            (target / f"{safe}.json").write_text(json.dumps(frame, default=str, indent=2), encoding="utf-8")
            return 1
    frame.to_parquet(target / f"{safe}.parquet", index=False)
    meta = {
        "rows": len(frame),
        "columns": list(frame.columns),
        "attrs": {str(k): str(v) for k, v in getattr(frame, "attrs", {}).items()},
    }
    (target / f"{safe}.meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return len(frame)


def load_symbols(root: Path) -> list[str]:
    p = root / "reference" / "historical_universe.parquet"
    if p.exists():
        df = pd.read_parquet(p, columns=["symbol"])
        return sorted({str(x).strip().upper() for x in df["symbol"].dropna() if str(x).strip()})
    ref = fs.fetch("reference", what="india")
    save_frame(root, "reference", "india", ref)
    col = "symbol" if "symbol" in ref.columns else next((c for c in ref.columns if "symbol" in c.lower()), None)
    if col is None:
        raise RuntimeError("Finstack India reference did not expose a symbol column")
    return sorted({str(x).strip().upper() for x in ref[col].dropna() if str(x).strip()})


def fetch_global(root: Path, start: date, end: date, manifest: dict) -> None:
    global_jobs = [
        ("reference", "reference_india", lambda: fs.fetch("reference", what="india")),
        ("indices", "NIFTY_50", lambda: fs.fetch("india_indices", "NIFTY 50", start=str(start), end=str(end))),
        ("indices", "NIFTY_500", lambda: fs.fetch("india_indices", "NIFTY 500", start=str(start), end=str(end))),
        ("indices", "NIFTY_MIDSMALLCAP_400", lambda: fs.fetch("india_indices", "NIFTY MIDSMALLCAP 400", start=str(start), end=str(end))),
        ("indices", "INDIA_VIX", lambda: fs.fetch("india_indices", "INDIA VIX", start=str(start), end=str(end))),
        ("index_constituents", "NIFTY_500_current", lambda: fs.fetch("india_indices", "NIFTY 500", what="constituents")),
        ("events", "results_calendar", lambda: fs.fetch("india_corporate_events", what="results_calendar", start=str(start), end=str(end))),
    ]
    for family, name, job in global_jobs:
        key = f"{family}/{name}"
        if key in manifest["completed"]:
            continue
        try:
            rows = save_frame(root, family, name, job())
            manifest["completed"][key] = {"rows": rows}
            print(json.dumps({"dataset": key, "rows": rows}), flush=True)
        except Exception as exc:
            manifest["failures"].append({"dataset": key, "code": type(exc).__name__, "message": str(exc)[:240]})
            print(json.dumps({"dataset": key, "failure": type(exc).__name__}), flush=True)
        atomic_json(root / "manifests" / "finstack.json", manifest)


def fetch_per_symbol(root: Path, start: date, end: date, symbols: list[str], shard: int, shards: int, manifest: dict) -> None:
    selected = [s for i, s in enumerate(symbols) if i % shards == shard]
    event_whats = ["actions", "shareholding", "result_filings", "annual_reports"]
    for n, symbol in enumerate(selected, 1):
        jobs = [
            ("financials_quarterly", lambda s=symbol: fs.fetch("india_company_financials", s, statement="quarterly")),
            ("financials_annual", lambda s=symbol: fs.fetch("india_company_financials", s, statement="annual")),
            ("company_profile", lambda s=symbol: fs.fetch("company_profile", s)),
        ]
        for what in event_whats:
            jobs.append((f"events_{what}", lambda w=what, s=symbol: fs.fetch("india_corporate_events", s, what=w, start=str(start), end=str(end))))
        for family, job in jobs:
            key = f"{family}/{symbol}"
            if key in manifest["completed"]:
                continue
            try:
                rows = save_frame(root, family, symbol, job())
                manifest["completed"][key] = {"rows": rows, "n": n, "of": len(selected)}
            except Exception as exc:
                manifest["failures"].append({"dataset": key, "code": type(exc).__name__, "message": str(exc)[:240]})
            atomic_json(root / "manifests" / f"finstack-{shard}-of-{shards}.json", manifest)
        print(json.dumps({"symbol": symbol, "n": n, "of": len(selected)}), flush=True)


def run(root: Path, start: date, end: date, shard: int, shards: int, global_only: bool) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifests" / ("finstack.json" if global_only else f"finstack-{shard}-of-{shards}.json")
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}
    manifest.setdefault("completed", {})
    manifest.setdefault("failures", [])
    manifest.update({"start": str(start), "end": str(end), "root": str(root), "shard": shard, "shards": shards})

    # Finstack provides shared pacing, caching, retries and source fallback. Keep its cache local to the machine.
    try:
        fs.configure(budgets={"nse": 2, "nse_archives": 1, "niftyindices": 1})
    except Exception:
        pass

    if global_only:
        fetch_global(root, start, end, manifest)
    else:
        symbols = load_symbols(root)
        fetch_per_symbol(root, start, end, symbols, shard, shards, manifest)
        manifest["symbols_total"] = len(symbols)
    atomic_json(manifest_path, manifest)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch DEEX supporting datasets with Finstack and save locally.")
    p.add_argument("--root", type=Path, default=ROOT / "local_data" / "deex")
    p.add_argument("--start", type=date.fromisoformat, default=date(2013, 10, 24))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--shards", type=int, default=8)
    p.add_argument("--global-only", action="store_true")
    args = p.parse_args()
    print(json.dumps(run(args.root, args.start, args.end, args.shard, args.shards, args.global_only), indent=2, default=str))


if __name__ == "__main__":
    main()
