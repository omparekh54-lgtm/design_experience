from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.data_layer.reference_gap_ingestion import ReferenceGapIngestor

router = APIRouter(prefix="/v1/execution/data-gaps", tags=["data-gaps"])


def _engine():
    from apps.api.main import db_engine
    return db_engine()


class SurveillanceBackfillRequest(BaseModel):
    start: date
    end: date


class ShareholdingBackfillRequest(BaseModel):
    symbol: str | None = Field(default=None, description="Optional NSE symbol. Omit for full universe.")


@router.post("/shareholding/backfill")
def backfill_shareholding(payload: ShareholdingBackfillRequest):
    try:
        return ReferenceGapIngestor(_engine()).ingest_shareholding(symbol=payload.symbol)
    except Exception as exc:  # admin operation should return an actionable error
        raise HTTPException(status_code=500, detail=f"shareholding backfill failed: {exc}") from exc


@router.post("/surveillance/backfill")
def backfill_surveillance(payload: SurveillanceBackfillRequest):
    if (payload.end - payload.start).days > 92:
        raise HTTPException(status_code=400, detail="backfill window must be 93 days or less per request")
    try:
        return ReferenceGapIngestor(_engine()).backfill_surveillance(payload.start, payload.end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"surveillance backfill failed: {exc}") from exc
