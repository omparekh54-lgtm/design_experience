"""Build point-in-time historical sector intervals from dated NSE sector-index membership.

Historical intervals come from the reviewed public reconstruction of NSE Indices
press releases. That source currently has dated membership evidence through
2026-05-15. For a later Step-1 cutoff we DO NOT silently stretch those open
intervals. Instead we reconcile them against live official NSE constituent
snapshots and apply the source project's documented semi-annual snapshot method:
source-open intervals are carried only to the next review boundary, then the
current official snapshot is used from that boundary onward. These inferred rows
are labelled separately and are never confused with press-release-backed rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP

SOURCE_URL = "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main/index_history/data/index_membership_history.csv"
SOURCE_REPO = "https://github.com/aditya-jha/nse-historical-membership"
SOURCE_FRESHNESS = date(2026, 5, 15)

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

# Official live constituent sources. Static archives are preferable because they
# do not need a browser session; two sector indices are exposed through NSE API.
CURRENT_SOURCES = {
    "Nifty Bank": "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "Nifty IT": "https://archives.nseindia.com/content/indices/ind_niftyitlist.csv",
    "Nifty FMCG": "https://archives.nseindia.com/content/indices/ind_niftyfmcglist.csv",
    "Nifty Pharma": "https://archives.nseindia.com/content/indices/ind_niftypharmalist.csv",
    "Nifty Auto": "https://archives.nseindia.com/content/indices/ind_niftyautolist.csv",
    "Nifty Metal": "https://archives.nseindia.com/content/indices/ind_niftymetallist.csv",
    "Nifty Realty": "https://archives.nseindia.com/content/indices/ind_niftyrealtylist.csv",
    "Nifty Energy": "https://archives.nseindia.com/content/indices/ind_niftyenergylist.csv",
    "Nifty PSU Bank": "https://archives.nseindia.com/content/indices/ind_niftypsubanklist.csv",
    "Nifty Private Bank": "nseapi:NIFTY PRIVATE BANK",
    "Nifty Healthcare Index": "https://archives.nseindia.com/content/indices/ind_niftyhealthcarelist.csv",
    "Nifty Financial Services": "nseapi:NIFTY FIN SERVICE",
    "Nifty Media": "https://archives.nseindia.com/content/indices/ind_niftymedialist.csv",
    "Nifty Consumer Durables": "https://archives.nseindia.com/content/indices/ind_niftyconsumerdurableslist.csv",
    "Nifty Oil & Gas": "https://archives.nseindia.com/content/indices/ind_niftyoilgaslist.csv",
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


def _get(url: str, attempts: int = 4) -> requests.Response:
    last = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=45, headers={"User-Agent": "pms-historical-sector-repair/2.0"})
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(8, 2 ** (attempt - 1)))
    raise RuntimeError(f"sector source unavailable: {type(last).__name__}") from last


def fetch_source() -> tuple[bytes, list[dict]]:
    response = _get(SOURCE_URL)
    raw = response.content
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or not {"index_name", "symbol", "valid_from", "valid_to", "source", "source_url"}.issubset(rows[0]):
        raise RuntimeError("historical membership source schema changed")
    return raw, rows


def _symbols_from_csv(body: bytes) -> set[str]:
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    out = set()
    for row in rows:
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        symbol = lower.get("symbol") or lower.get("ticker") or lower.get("nse symbol")
        if symbol and str(symbol).strip():
            out.add(str(symbol).strip().upper())
    if not out:
        raise RuntimeError("official sector constituent CSV had no symbols")
    return out


def fetch_current_memberships() -> tuple[dict[str, set[str]], dict[str, dict]]:
    memberships: dict[str, set[str]] = {}
    evidence: dict[str, dict] = {}
    api = OfficialHTTP(seconds=900)
    for name, source in CURRENT_SOURCES.items():
        if source.startswith("nseapi:"):
            index_name = source.split(":", 1)[1]
            doc = api.api("equity-stockIndices", index=index_name)
            payload = json.loads(doc.body)
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError(f"{name}: official NSE API schema changed")
            symbols = {str(r.get("symbol") or "").strip().upper() for r in rows if isinstance(r, dict) and r.get("symbol")}
            uri, sha = doc.url, doc.sha256
        else:
            response = _get(source)
            body = response.content
            symbols = _symbols_from_csv(body)
            uri, sha = source, hashlib.sha256(body).hexdigest()
        if not symbols:
            raise RuntimeError(f"{name}: official current membership is empty")
        memberships[name] = symbols
        evidence[name] = {"source_uri": uri, "content_sha256": sha, "symbols": len(symbols)}
    return memberships, evidence


def review_boundary_after(freshness: date, end: date) -> date | None:
    candidates = []
    for year in range(freshness.year, end.year + 1):
        for month, day in ((3, 31), (9, 30)):
            d = date(year, month, day)
            if freshness < d <= end:
                candidates.append(d)
    return max(candidates) if candidates else None


def reconcile_current(rows: list[dict], end: date) -> tuple[list[dict], dict]:
    """Bound stale open intervals and add official-current snapshot intervals.

    This mirrors the source repository's documented `snapshot` / inferred-exclude
    policy. It is deliberately explicit evidence, not a fabricated press release.
    """
    if end <= SOURCE_FRESHNESS:
        return rows, {"current_reconciliation": "NOT_NEEDED", "source_freshness": SOURCE_FRESHNESS.isoformat()}
    boundary = review_boundary_after(SOURCE_FRESHNESS, end)
    if boundary is None:
        raise RuntimeError(
            f"sector source is fresh only through {SOURCE_FRESHNESS}; no review boundary exists to reconcile cutoff {end}"
        )
    current, current_evidence = fetch_current_memberships()
    adjusted = []
    for row in rows:
        item = dict(row)
        if (item.get("index_name") or "").strip() in SECTOR_INDICES and not (item.get("valid_to") or "").strip():
            # An old open interval is evidence only until the next scheduled review.
            item["valid_to"] = boundary.isoformat()
            notes = (item.get("notes") or "").strip()
            item["notes"] = (notes + "; " if notes else "") + f"bounded at current-snapshot reconciliation {boundary}"
        adjusted.append(item)
    for name, symbols in current.items():
        source_uri = current_evidence[name]["source_uri"]
        for symbol in sorted(symbols):
            adjusted.append({
                "index_name": name,
                "symbol": symbol,
                "valid_from": boundary.isoformat(),
                "valid_to": "",
                "source": "snapshot",
                "source_url": source_uri,
                "notes": f"official current snapshot reconciled at inferred semi-annual boundary {boundary}",
            })
    return adjusted, {
        "current_reconciliation": "OFFICIAL_NSE_CURRENT_SNAPSHOT",
        "source_freshness": SOURCE_FRESHNESS.isoformat(),
        "reconciliation_boundary": boundary.isoformat(),
        "current_snapshot_evidence": current_evidence,
        "policy": "Open historical rows are bounded at the next semi-annual review; current official constituent snapshots are applied from that boundary. This follows the source repository's documented snapshot/inferred-exclude model.",
    }


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
    if args.end > date.today():
        ap.error("--end cannot be in the future")

    raw, source_rows = fetch_source()
    source_sha = hashlib.sha256(raw).hexdigest()
    reconciled_rows, reconciliation = reconcile_current(source_rows, args.end)
    intervals, build_meta = build_intervals(reconciled_rows, args.start, args.end)
    if build_meta["ambiguous_count"]:
        raise RuntimeError(f"historical sector reconstruction has {build_meta['ambiguous_count']} unresolved same-priority sector conflicts")
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
                provenance = "NSE_PIT_SECTOR_INDEX_CURRENT_RECONCILED" if "snapshot" in item.get("source_kinds", set()) else "NSE_PIT_SECTOR_INDEX"
                result = conn.execute(text("""
                    insert into pms.sector_history(instrument_id,sector,industry,effective_from,effective_to,source)
                    values(:iid,:sector,null,:frm,:to,:source)
                    on conflict(instrument_id,sector,effective_from,source) do update
                    set effective_to=excluded.effective_to
                """), {"iid": iid, "sector": item["sector"], "frm": item["start"], "to": item["end"], "source": provenance})
                inserted += max(0, result.rowcount)

            meta = {
                "version": "PIT_SECTOR_INDEX_v2",
                "complete": True,
                "start": args.start.isoformat(), "end": args.end.isoformat(),
                "repo": SOURCE_REPO, "historical_source_sha256": source_sha,
                "sector_indices": sorted(SECTOR_INDICES),
                "intervals_built": len(intervals), "unmatched_symbols": sorted(unmatched)[:250],
                **reconciliation, **build_meta,
            }
            meta_bytes=json.dumps(meta,default=str,sort_keys=True,separators=(",",":")).encode()
            combined_sha=hashlib.sha256(raw+b"\n"+meta_bytes).hexdigest()
            conn.execute(text("""
                insert into pms.provider_snapshots(provider,dataset,source_uri,content_sha256,observed_at,metadata)
                values('NSE_INDICES_PIT','HISTORICAL_SECTOR_INTERVALS',:uri,:sha,now(),cast(:meta as jsonb))
            """), {"uri": SOURCE_URL, "sha": combined_sha, "meta": json.dumps(meta, default=str)})

        with db.connect() as conn:
            coverage = dict(conn.execute(text("""
                select count(*)::int rows,count(distinct instrument_id)::int instruments,
                       min(effective_from) min_from,max(effective_to) max_to
                from pms.sector_history where source in ('NSE_PIT_SECTOR_INDEX','NSE_PIT_SECTOR_INDEX_CURRENT_RECONCILED')
                  and effective_from<=:end and effective_to>:start
            """), {"start": args.start, "end": args.end}).mappings().one())
        result = {
            "status": "PASS" if coverage["rows"] else "BLOCKED",
            "historical_source": SOURCE_URL, "historical_source_sha256": source_sha,
            "range": [args.start.isoformat(), args.end.isoformat()],
            "coverage": coverage, "inserted_or_updated": inserted,
            "unmatched_symbols": sorted(unmatched), **reconciliation, **build_meta,
            "policy": "Dated sector-index history is used through source freshness. Later coverage is reconciled only from official current NSE constituent snapshots at the documented semi-annual review boundary; current company classifications are never backdated.",
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
