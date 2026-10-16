from __future__ import annotations

from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from packages.pms.safety import SafetyController
from packages.pms.portfolio_accounting import PortfolioAccountingService
from packages.pms.brokers import PaperBroker
from packages.pms.reconciliation import ReconciliationService

router = APIRouter(prefix="/v1/execution", tags=["execution"])


class StopRequest(BaseModel):
    reason: str = "operator_emergency_stop"


class ModeRequest(BaseModel):
    mode: str


def _engine():
    from apps.api.main import db_engine
    return db_engine()


@router.get("/safety")
def safety_status():
    s = SafetyController(_engine()).status()
    return {
        "execution_mode": s.execution_mode,
        "new_orders_enabled": s.new_orders_enabled,
        "emergency_stop": s.emergency_stop,
        "locked_reason": s.locked_reason,
        "live_auto_enabled": False,
        "can_place_live_order": s.can_place_live_order,
    }


@router.post("/emergency-stop")
def emergency_stop(payload: StopRequest):
    SafetyController(_engine()).emergency_stop(payload.reason, actor="api_operator")
    return {"status": "stopped", "reason": payload.reason}


@router.post("/mode")
def set_mode(payload: ModeRequest):
    try:
        SafetyController(_engine()).set_mode(payload.mode.upper(), actor="api_operator")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"execution_mode": payload.mode.upper(), "new_orders_enabled": False, "live_auto_enabled": False}


@router.get("/reconciliation/latest")
def latest_reconciliation():
    with _engine().connect() as conn:
        row = conn.execute(text("select * from reconciliation_runs order by started_at desc limit 1")).mappings().first()
    return dict(row) if row else {"status": "never_run"}


@router.post("/reconciliation/paper")
def reconcile_paper():
    engine = _engine()
    result = ReconciliationService(engine, PaperBroker(engine)).run("PAPER")
    return {"run_id": result.run_id, "ok": result.ok, "discrepancies": list(result.discrepancies)}


@router.get("/portfolio-snapshot/{snapshot_date}")
def portfolio_snapshot(snapshot_date: date):
    snap = PortfolioAccountingService(_engine()).snapshot(snapshot_date, "PAPER")
    return {
        "snapshot_date": snap.snapshot_date,
        "account_mode": snap.account_mode,
        "cash": snap.cash,
        "market_value": snap.market_value,
        "realized_pnl": snap.realized_pnl,
        "unrealized_pnl": snap.unrealized_pnl,
        "nav": snap.nav,
        "open_risk": snap.open_risk,
        "portfolio_heat_pct": snap.portfolio_heat_pct,
        "positions": [p.__dict__ for p in snap.positions],
    }


@router.get("/workers")
def worker_runs():
    with _engine().connect() as conn:
        rows = conn.execute(text("select * from worker_runs order by started_at desc limit 50")).mappings().all()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}
