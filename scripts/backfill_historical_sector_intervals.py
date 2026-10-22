"""Build point-in-time historical sector intervals from dated NSE sector-index membership.

The source dataset is reconstructed from NSE Indices press releases/circulars and
keeps source_url provenance per membership interval. We deliberately do NOT
backdate today's company classification. Stocks that cannot be classified from a
historical sector index remain missing and therefore ineligible wherever a dated
sector is required.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.main import _render_safe_database_url

SOURCE_URL = "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main/index_history/data/index_membership_history.csv"
SOURCE_REPO = "https://github.com/aditya-jha/nse-historical-membership"

# More specific indices win when a stock legitimately belongs to two sector
# indices (e.g. Bank + Financial Services, Pharma + Healthcare).
SECTOR_INDICES = {
    "Nifty PSU Bank": (10, "PSU Bank"),
    "Nifty Private Bank": (11, "Private Bank"),
    "Nifty Bank": (20, "Banking"),
    "Nifty Pharma": (21, "Pharmaceuticals"),
    "Nifty Healthcare Index": (30, "Healthcare"),
    "Nifty IT": (20, "Information Technology"),
    "Nifty Auto": (20, "Automobile"),
    "Nifty FMCG": (20, "FMCG"),
    "Nifty Metal": (20, "Metals"),
    "Nifty Realty": (20, "Realty"),
    "Nifty Media": (20, "Media"),
    "Nifty Consumer Durables": (20, "Consumer Durables"),
    "Nifty Oil & Gas": (21, "Oil & Gas"),
    "Nifty Energy": (30, "Energy"),
    "Nifty Financial Services": (40, "Financial Services"),
}


def parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def engine():
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL or PMS_DATABASE_URL is required in .env")
    return create_engine(
        _render_safe_database_url(raw), pool_pre_ping=True,
        connect_args={"options": "-c search_path=pms,public"},
    )


def fetch_source() -> tuple[bytes, list[dict]]:
    response = requests.get(SOURCE_URL, timeout=90, headers={"User-Agent": "pms-historical-sector-repair/1.0"})
    response.raise_for_status()
    raw = response.content
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or not {"index_name", "symbol", "valid_from", "valid_to", "source", "source_url"}.issubset(rows[0]):
        raise RuntimeError("historical membership source schema changed")
    return raw, rows


def resolve_symbol_map(conn) -> dict[str, int]:
    rows = conn.execute(text("""
        select id,upper(coalesce(nse_symbol,replace(trading_symbol,'-EQ',''))) symbol
        from pms.instruments
        where exchange='NSE' and metadata->>'ingestion_duplicate_of' is null
    """)).all()
    mapping: dict[str, int] = {}
    dupes: set[str] = set()
    for iid, symbol in rows:
        if not symbol:
            continue
        if symbol in mapping and mapping[symbol] != int(iid):
            dupes.add(symbol)
        else:
            mapping[symbol] = int(iid)
    for symbol in dupes:
        mapping.pop(symbol, None)
    return mapping


def build_intervals(rows: list[dict], start: date, end: date) -> tuple[list[dict], dict]:
    # Each candidate interval retains its actual source URL. We then sweep every
    # change boundary to choose one deterministic, non-overlapping sector proxy.
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    accepted_source_kinds = {"press_release", "circular", "merger", "snapshot_floor", "snapshot", "inferred-exclude"}
    for row in rows:
        name = (row.get("index_name") or "").strip()
        if name not in SECTOR_INDICES:
            continue
        source_kind = (row.get("source") or "").strip()
        if source_kind not in accepted_source_kinds:
            continue
        frm = parse_date(row.get("valid_from"))
        to = parse_date(row.get("valid_to"))
        if frm is None:
            continue
        lo = max(frm, start)
        hi = min(to or (end + timedelta(days=1)), end + timedelta(days=1))
        if lo >= hi:
            continue
        priority, sector = SECTOR_INDICES[name]
        by_symbol[(row.get("symbol") or "").strip().upper()].append({
            "start": lo, "end": hi, "priority": priority, "sector": sector,
            "index_name": name, "source_kind": source_kind,
            "source_url": (row.get("source_url") or "").strip(),
            "notes": (row.get("notes") or "").strip(),
        })

    output: list[dict] = []
    ambiguous = []
    for symbol, candidates in sorted(by_symbol.items()):
        boundaries = sorted({x["start"] for x in candidates} | {x["end"] for x in candidates})
        segments: list[dict] = []
        for a, b in zip(boundaries, boundaries[1:]):
            active = [x for x in candidates if x["start"] <= a and x["end"] >= b]
            if not active:
                continue
            active.sort(key=lambda x: (x["priority"], x["sector"], x["index_name"]))
            chosen = active[0]
            tied = [x for x in active if x["priority"] == chosen["priority"] and x["sector"] != chosen["sector"]]
            if tied:
                ambiguous.append({"symbol": symbol, "from": a.isoformat(), "to": b.isoformat(), "candidates": sorted({x["sector"] for x in active})})
                continue
            segment = {**chosen, "symbol": symbol, "start": a, "end": b}
            if segments and segments[-1]["sector"] == segment["sector"] and segments[-1]["end"] == segment["start"]:
                segments[-1]["end"] = segment["end"]
                segments[-1]["source_urls"].update([u for u in [segment["source_url"]] if u])
                segments[-1]["source_kinds"].add(segment["source_kind"])
            else:
                segment["source_urls"] = set([segment["source_url"]]) if segment["source_url"] else set()
                segment["source_kinds"] = {segment["source_kind"]}
                segments.append(segment)
        output.extend(segments)
    return output, {"symbols_with_candidates": len(by_symbol), "ambiguous_segments_skipped": ambiguous[:100], "ambiguous_count": len(ambiguous)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=date(2017, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    ap.add_argument("--output", default="artifacts/final_acceptance/step1/historical-sectors.json")
    args = ap.parse_args()
    if args.end < args.start:
        ap.error("--end must be on or after --start")

    raw, source_rows = fetch_source()
    source_sha = hashlib.sha256(raw).hexdigest()
    intervals, build_meta = build_intervals(source_rows, args.start, args.end)
    db = engine()
    inserted = 0
    unmatched = set()
    try:
        with db.begin() as conn:
            symbols = resolve_symbol_map(conn)
            for item in intervals:
                iid = symbols.get(item["symbol"])
                if iid is None:
                    unmatched.add(item["symbol"])
                    continue
                # Historical import ends no later than the declared research boundary,
                # so it cannot overlap today's separately sourced classification row.
                effective_to = item["end"]
                source = "NSE_PIT_SECTOR_INDEX"
                result = conn.execute(text("""
                    insert into pms.sector_history(instrument_id,sector,industry,effective_from,effective_to,source)
                    values(:iid,:sector,null,:frm,:to,:source)
                    on conflict(instrument_id,sector,effective_from,source) do update
                    set effective_to=excluded.effective_to
                """), {"iid": iid, "sector": item["sector"], "frm": item["start"], "to": effective_to, "source": source})
                inserted += max(0, result.rowcount)

            # Keep an immutable source receipt in the existing provider provenance table.
            meta = {
                "version": "PIT_SECTOR_INDEX_v1",
                "start": args.start.isoformat(), "end": args.end.isoformat(),
                "repo": SOURCE_REPO, "source_sha256": source_sha,
                "sector_indices": sorted(SECTOR_INDICES),
                "intervals_built": len(intervals), "unmatched_symbols": sorted(unmatched)[:250],
                **build_meta,
            }
            conn.execute(text("""
                insert into pms.provider_snapshots(provider,dataset,source_uri,content_sha256,observed_at,complete,metadata)
                values('NSE_INDICES_PIT','HISTORICAL_SECTOR_INTERVALS',:uri,:sha,now(),true,cast(:meta as jsonb))
            """), {"uri": SOURCE_URL, "sha": source_sha, "meta": json.dumps(meta, default=str)})

        with db.connect() as conn:
            coverage = dict(conn.execute(text("""
                select count(*)::int rows,count(distinct instrument_id)::int instruments,
                       min(effective_from) min_from,max(effective_to) max_to
                from pms.sector_history where source='NSE_PIT_SECTOR_INDEX'
                  and effective_from<=:end and effective_to>:start
            """), {"start": args.start, "end": args.end}).mappings().one())
        result = {
            "status": "PASS" if coverage["rows"] else "BLOCKED",
            "source": SOURCE_URL, "source_sha256": source_sha,
            "range": [args.start.isoformat(), args.end.isoformat()],
            "coverage": coverage, "inserted_or_updated": inserted,
            "unmatched_symbols": sorted(unmatched), **build_meta,
            "policy": "Only dated sector-index membership is used historically; current company classifications are never backdated. Missing historical sector remains missing/ineligible.",
        }
        out = ROOT / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, default=str))
        return 0 if coverage["rows"] else 2
    finally:
        db.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
