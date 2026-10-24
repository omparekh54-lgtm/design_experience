from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data_layer.cloud_sources import OfficialHTTP, SourceError
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, filings_from_exchange_record
from packages.pms.historical_dataset import discover_financial_history

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def serialise(value):
    if is_dataclass(value):
        return {k: serialise(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialise(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def load_universe(root: Path) -> pd.DataFrame:
    path = root / "reference" / "historical_universe.parquet"
    if not path.exists():
        raise RuntimeError(f"historical universe not found: {path}; run fetch_deex_local_market_data.py first")
    df = pd.read_parquet(path)
    df["first_trade"] = pd.to_datetime(df["first_trade"], errors="coerce")
    df["last_trade"] = pd.to_datetime(df["last_trade"], errors="coerce")
    return df


def symbols_for_window(root: Path, start: date, end: date) -> list[str]:
    df = load_universe(root)
    mask = (df["first_trade"].dt.date <= end) & (df["last_trade"].dt.date >= start)
    symbols = sorted({str(s).strip().upper() for s in df.loc[mask, "symbol"].dropna() if str(s).strip()})
    return symbols


def save_raw(root: Path, symbol: str, external_id: str, uri: str, body: bytes) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in external_id)[:160]
    ext = ".html" if uri.lower().endswith(".html") else ".xml"
    path = root / "raw" / "financials" / symbol / f"{safe}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
    return path


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def run(root: Path, start: date, end: date, shard: int, shards: int) -> dict:
    if not 0 <= shard < shards:
        raise ValueError("invalid shard")
    root = root.resolve()
    all_symbols = symbols_for_window(root, start, end)
    symbols = [s for i, s in enumerate(all_symbols) if i % shards == shard]
    manifest_path = root / "manifests" / f"financials-{shard}-of-{shards}.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}
    completed = set(manifest.get("completed_symbols") or [])
    manifest.update({"start": str(start), "end": str(end), "shard": shard, "shards": shards, "symbols": len(symbols)})
    manifest.setdefault("completed_symbols", sorted(completed))
    manifest.setdefault("failures", [])
    manifest.setdefault("records_discovered", 0)
    manifest.setdefault("filings_parsed", 0)
    provider = NSEIntegratedFinancialsProvider()

    for n, symbol in enumerate(symbols, 1):
        if symbol in completed:
            continue
        try:
            records = discover_financial_history(provider, symbol, start, end, page_size=200, max_pages=100)
            record_rows = []
            filing_rows = []
            for rec in records:
                record_rows.append(serialise(rec))
                if not rec.xbrl_uri:
                    continue
                # One HTTP instance per filing prevents the previous 256 MB per-run budget from killing a long shard.
                http = OfficialHTTP(seconds=300)
                doc = http.fetch(rec.xbrl_uri, financial_render=rec.xbrl_uri.endswith("_iXBRL_WEB.html"))
                raw_path = save_raw(root, symbol, str(rec.external_id), rec.xbrl_uri, doc.body)
                parsed = filings_from_exchange_record(rec, doc.body)
                for item in parsed:
                    row = serialise(item)
                    if isinstance(row, dict):
                        row["raw_file"] = str(raw_path)
                    filing_rows.append(row)
            symbol_dir = root / "normalized" / "financials" / symbol
            if record_rows:
                append_jsonl(symbol_dir / "exchange_records.jsonl", record_rows)
            if filing_rows:
                append_jsonl(symbol_dir / "filings.jsonl", filing_rows)
            completed.add(symbol)
            manifest["completed_symbols"] = sorted(completed)
            manifest["records_discovered"] = int(manifest.get("records_discovered", 0)) + len(record_rows)
            manifest["filings_parsed"] = int(manifest.get("filings_parsed", 0)) + len(filing_rows)
            manifest["last_symbol"] = {"symbol": symbol, "n": n, "of": len(symbols), "records": len(record_rows), "filings": len(filing_rows)}
            atomic_json(manifest_path, manifest)
            print(json.dumps(manifest["last_symbol"]), flush=True)
        except SourceError as exc:
            manifest["failures"].append({"symbol": symbol, "code": str(exc)})
            atomic_json(manifest_path, manifest)
            print(json.dumps({"symbol": symbol, "failure": str(exc)}), flush=True)
        except Exception as exc:
            manifest["failures"].append({"symbol": symbol, "code": type(exc).__name__})
            atomic_json(manifest_path, manifest)
            print(json.dumps({"symbol": symbol, "failure": type(exc).__name__}), flush=True)

    manifest["completed"] = len(completed)
    manifest["remaining"] = len(symbols) - len(completed)
    atomic_json(manifest_path, manifest)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch DEEX historical NSE financial filings directly to local disk; no database required.")
    p.add_argument("--root", type=Path, default=ROOT / "local_data" / "deex")
    p.add_argument("--start", type=date.fromisoformat, default=date(2013, 10, 24))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--shards", type=int, default=8)
    args = p.parse_args()
    print(json.dumps(run(args.root, args.start, args.end, args.shard, args.shards), indent=2, default=str))


if __name__ == "__main__":
    main()
