from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data_layer.cloud_sources import OfficialHTTP, SourceError, bhavcopy_url, parse_bhavcopy, parse_indices

ROOT = Path(__file__).resolve().parents[1]
SERIES = {"EQ", "BE", "BZ", "SM", "ST", "SZ"}
INDEX_URL = "https://archives.nseindia.com/content/indices/ind_close_all_{stamp}.csv"


def legacy_url(day: date) -> str:
    return (
        "https://archives.nseindia.com/content/historical/EQUITIES/"
        + day.strftime("%Y/%b").upper()
        + "/cm"
        + day.strftime("%d%b%Y").upper()
        + "bhav.csv.zip"
    )


def parse_legacy(body: bytes, day: date) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        expected = "cm" + day.strftime("%d%b%Y").upper() + "bhav.csv"
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected:
            raise SourceError("legacy_bhavcopy_archive_invalid")
        raw = archive.read(members[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    required = {"SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP", "ISIN"}
    if not reader.fieldnames or not required.issubset({x.strip() for x in reader.fieldnames}):
        raise SourceError("legacy_bhavcopy_schema_changed")
    out = []
    for row in reader:
        r = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        if r["SERIES"] not in SERIES:
            continue
        source_day = datetime.strptime(r["TIMESTAMP"], "%d-%b-%Y").date()
        if source_day != day:
            raise SourceError("legacy_bhavcopy_wrong_date")
        def num(name: str, integer: bool = False):
            v = float(str(r[name]).replace(",", ""))
            return int(v) if integer else v
        o, h, l, c = [num(k) for k in ("OPEN", "HIGH", "LOW", "CLOSE")]
        volume = num("TOTTRDQTY", True)
        if volume == 0 and not any((o, h, l, c)):
            continue
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
            raise SourceError("legacy_bhavcopy_invalid_ohlc")
        out.append({
            "symbol": r["SYMBOL"], "series": r["SERIES"], "isin": r["ISIN"],
            "trade_date": day, "open": o, "high": h, "low": l, "close": c,
            "volume": volume, "turnover": num("TOTTRDVAL")
        })
    if not out:
        raise SourceError("legacy_bhavcopy_no_equities")
    return out


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def save_raw(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
    return hashlib.sha256(body).hexdigest()


def daterange(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def fetch_price_day(day: date, raw_dir: Path):
    http = OfficialHTTP(seconds=180)
    try:
        doc = http.fetch(bhavcopy_url(day))
        rows = parse_bhavcopy(doc.body, day)
        fmt = "UDIFF"
        suffix = "udiff.zip"
    except SourceError as exc:
        if str(exc) not in {"source_http_404", "bhavcopy_archive_invalid", "csv_schema_changed", "csv_header_invalid"}:
            raise
        http = OfficialHTTP(seconds=180)
        doc = http.fetch(legacy_url(day))
        rows = parse_legacy(doc.body, day)
        fmt = "LEGACY"
        suffix = "legacy.zip"
    sha = save_raw(raw_dir / str(day.year) / f"{day.isoformat()}-{suffix}", doc.body)
    return rows, fmt, doc.url, sha


def fetch_index_day(day: date, raw_dir: Path):
    http = OfficialHTTP(seconds=120)
    doc = http.fetch(INDEX_URL.format(stamp=day.strftime("%d%m%Y")))
    rows = parse_indices(doc.body, day)
    sha = save_raw(raw_dir / str(day.year) / f"{day.isoformat()}-indices.csv", doc.body)
    return rows, doc.url, sha


def write_price_partition(root: Path, day: date, rows: list[dict]) -> Path:
    target = root / "normalized" / "prices" / f"year={day.year}" / f"month={day.month:02d}" / f"{day.isoformat()}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.to_parquet(target, index=False)
    return target


def write_index_partition(root: Path, day: date, rows: list[dict]) -> Path:
    target = root / "normalized" / "indices" / f"year={day.year}" / f"{day.isoformat()}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.to_parquet(target, index=False)
    return target


def rebuild_universe(root: Path) -> None:
    files = sorted((root / "normalized" / "prices").glob("year=*/month=*/*.parquet"))
    if not files:
        return
    frames = []
    for p in files:
        frames.append(pd.read_parquet(p, columns=["symbol", "isin", "series", "trade_date"]))
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    universe = (
        df.groupby(["symbol", "isin", "series"], dropna=False)["trade_date"]
          .agg(first_trade="min", last_trade="max", sessions="nunique")
          .reset_index()
          .sort_values(["symbol", "first_trade", "isin"])
    )
    out = root / "reference" / "historical_universe.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(out, index=False)
    universe.to_csv(root / "reference" / "historical_universe.csv", index=False)


def run(start: date, end: date, root: Path, prices: bool, indices: bool) -> dict:
    root = root.resolve()
    manifest_path = root / "manifests" / "market-data.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}
    manifest.setdefault("start", str(start))
    manifest.setdefault("end", str(end))
    manifest.setdefault("price_completed", [])
    manifest.setdefault("index_completed", [])
    manifest.setdefault("holidays_or_missing", [])
    manifest.setdefault("failures", [])
    p_done = set(manifest["price_completed"])
    i_done = set(manifest["index_completed"])

    for n, day in enumerate(daterange(start, end), 1):
        key = day.isoformat()
        if prices and key not in p_done:
            try:
                rows, fmt, url, sha = fetch_price_day(day, root / "raw" / "prices")
                path = write_price_partition(root, day, rows)
                p_done.add(key)
                manifest["price_completed"] = sorted(p_done)
                manifest["last_price"] = {"date": key, "rows": len(rows), "format": fmt, "source": url, "sha256": sha, "file": str(path)}
                atomic_json(manifest_path, manifest)
                print(json.dumps({"type": "prices", "date": key, "rows": len(rows), "format": fmt}), flush=True)
            except SourceError as exc:
                if str(exc) == "source_http_404":
                    manifest["holidays_or_missing"].append({"type": "prices", "date": key, "code": str(exc)})
                else:
                    manifest["failures"].append({"type": "prices", "date": key, "code": str(exc)})
                atomic_json(manifest_path, manifest)
                print(json.dumps({"type": "prices", "date": key, "status": str(exc)}), flush=True)
            except Exception as exc:
                manifest["failures"].append({"type": "prices", "date": key, "code": type(exc).__name__})
                atomic_json(manifest_path, manifest)
                print(json.dumps({"type": "prices", "date": key, "status": type(exc).__name__}), flush=True)

        if indices and key not in i_done:
            try:
                rows, url, sha = fetch_index_day(day, root / "raw" / "indices")
                path = write_index_partition(root, day, rows)
                i_done.add(key)
                manifest["index_completed"] = sorted(i_done)
                manifest["last_index"] = {"date": key, "rows": len(rows), "source": url, "sha256": sha, "file": str(path)}
                atomic_json(manifest_path, manifest)
                print(json.dumps({"type": "indices", "date": key, "rows": len(rows)}), flush=True)
            except SourceError as exc:
                if str(exc) != "source_http_404":
                    manifest["failures"].append({"type": "indices", "date": key, "code": str(exc)})
                atomic_json(manifest_path, manifest)
            except Exception as exc:
                manifest["failures"].append({"type": "indices", "date": key, "code": type(exc).__name__})
                atomic_json(manifest_path, manifest)

    if prices:
        rebuild_universe(root)
    manifest["price_completed_count"] = len(p_done)
    manifest["index_completed_count"] = len(i_done)
    atomic_json(manifest_path, manifest)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch DEEX historical market data directly to local disk; no database required.")
    p.add_argument("--start", type=date.fromisoformat, default=date(2014, 10, 24))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--root", type=Path, default=ROOT / "local_data" / "deex")
    p.add_argument("--skip-prices", action="store_true")
    p.add_argument("--skip-indices", action="store_true")
    args = p.parse_args()
    result = run(args.start, args.end, args.root, not args.skip_prices, not args.skip_indices)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
