from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
from sqlalchemy import Engine, text


@dataclass(frozen=True)
class DatasetManifest:
    manifest_id: str
    root_path: str
    start_date: date
    end_date: date
    universe: str
    tables: dict[str, dict]


EXPORTS = {
    "daily_prices": "select * from daily_prices where trade_date between :start and :end order by instrument_id, trade_date",
    "financial_filings": "select * from financial_filings where period_end between :start and :end or filing_timestamp::date between :start and :end order by instrument_id, period_end, filing_timestamp",
    "financial_facts": "select ff.* from financial_facts ff join financial_filings f on f.id=ff.filing_id where f.filing_timestamp::date<=:end order by ff.filing_id, ff.fact_name",
    "shareholding_history": "select * from shareholding_history where filed_at::date<=:end order by instrument_id, period_end, filed_at",
    "surveillance_history": "select * from surveillance_history where effective_from::date<=:end order by instrument_id, effective_from",
    "index_constituents_history": "select * from index_constituents_history where effective_from<=:end and (effective_to is null or effective_to>=:start) order by index_code, instrument_id, effective_from",
    "corporate_actions": "select * from corporate_actions where ex_date between :start and :end order by instrument_id, ex_date",
    "earnings_calendar": "select * from earnings_calendar where event_date between :start and :end order by instrument_id, event_date",
    "market_regime_history": "select * from market_regime_history where trade_date between :start and :end order by trade_date",
}


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_research_dataset(
    engine: Engine,
    root: str | Path,
    start: date,
    end: date,
    *,
    universe: str = "NSE_EQUITY",
) -> DatasetManifest:
    root = Path(root)
    manifest_id = str(uuid.uuid4())
    out = root / manifest_id
    out.mkdir(parents=True, exist_ok=True)
    table_meta: dict[str, dict] = {}

    with engine.connect() as conn:
        for name, sql in EXPORTS.items():
            rows = conn.execute(text(sql), {"start": start, "end": end}).mappings().all()
            normalized = [{k: _jsonable(v) for k, v in dict(row).items()} for row in rows]
            frame = pl.DataFrame(normalized) if normalized else pl.DataFrame()
            path = out / f"{name}.parquet"
            frame.write_parquet(path)
            table_meta[name] = {
                "rows": len(normalized),
                "file": path.name,
                "sha256": _checksum(path),
            }

    manifest = DatasetManifest(manifest_id, str(out), start, end, universe, table_meta)
    (out / "manifest.json").write_text(json.dumps({
        "manifest_id": manifest_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "universe": universe,
        "tables": table_meta,
    }, indent=2), encoding="utf-8")
    return manifest
