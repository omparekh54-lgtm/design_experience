from __future__ import annotations

from datetime import date, datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from packages.pms.audit import AuditTrail
from packages.pms.brokers import BrokerOrderRequest, PaperBroker
from packages.pms.live_validation import LiveValidationLimits, LiveValidationService
from packages.pms.paper_execution import PaperExecutionService
from packages.pms.paper_validation import PaperValidationService
from packages.pms.portfolio_accounting import PortfolioAccountingService
from packages.pms.production_readiness import ProductionReadinessController
from packages.pms.reconciliation import ReconciliationService
from packages.pms.safety import SafetyController

router = APIRouter(prefix="/v1/execution", tags=["execution"])


class StopRequest(BaseModel):
    reason: str = "operator_emergency_stop"


class ModeRequest(BaseModel):
    mode: str


class PaperValidationRequest(BaseModel):
    started_on: date
    ended_on: date


class PaperSandboxOrderRequest(BaseModel):
    instrument_id: str
    side: str
    quantity: int = Field(gt=0)
    order_type: str = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)
    trigger_price: float | None = Field(default=None, gt=0)


class PaperSandboxProcessRequest(BaseModel):
    trade_date: date | None = None


class LiveSessionRequest(BaseModel):
    max_capital: float
    max_order_value: float
    max_open_positions: int = 2
    max_daily_loss: float
    allowed_symbols: list[str] = Field(default_factory=list)
    notes: str | None = None


class LiveProposalRequest(BaseModel):
    instrument_id: str
    side: str
    quantity: int
    estimated_price: float
    order_type: str = "MARKET"
    limit_price: float | None = None
    trigger_price: float | None = None
    reason: str | None = None


class LiveObservationRequest(BaseModel):
    expected_price: float
    fill_price: float
    quantity: int
    executed_at: datetime
    fees: float = 0.0
    external_order_reference: str | None = None
    reconciliation_ok: bool = False
    notes: str | None = None


class RejectRequest(BaseModel):
    reason: str


def _engine():
    from apps.api.main import db_engine
    return db_engine()


def _sandbox_broker():
    return PaperBroker(_engine(), account_mode="PAPER_SANDBOX", broker_name="PAPER_SANDBOX")


@router.get("/safety")
def safety_status():
    s = SafetyController(_engine()).status()
    return {
        "execution_mode": s.execution_mode,
        "new_orders_enabled": s.new_orders_enabled,
        "emergency_stop": s.emergency_stop,
        "locked_reason": s.locked_reason,
        "live_auto_enabled": False,
        "can_place_live_order": False,
        "can_place_validation_order": s.can_place_validation_order,
        "real_money_execution_available": False,
        "data_provider": "FINSTACK_OPEN_SOURCE",
    }


@router.get("/paper-sandbox/account")
def paper_sandbox_account():
    broker = _sandbox_broker()
    with _engine().connect() as conn:
        latest_trade_date = conn.execute(text("select max(trade_date) from daily_prices")).scalar_one_or_none()
        fills = int(conn.execute(text("""
            select count(*) from broker_fills f
            join broker_orders o on o.id=f.order_id
            where o.mode='PAPER_SANDBOX'
        """)).scalar_one())
        realized = float(conn.execute(text("""
            select coalesce(sum(realized_pnl_delta),0)
            from portfolio_ledger where account_mode='PAPER_SANDBOX'
        """)).scalar_one())
    return {
        "account_mode": "PAPER_SANDBOX",
        "starting_cash": broker.initial_cash,
        "cash": broker.get_cash(),
        "positions": broker.get_positions(),
        "orders": broker.get_orders()[:100],
        "fills": fills,
        "realized_pnl": realized,
        "latest_market_date": latest_trade_date,
        "real_money": False,
        "formal_validation_evidence": False,
    }


@router.post("/paper-sandbox/orders")
def create_paper_sandbox_order(payload: PaperSandboxOrderRequest):
    request = BrokerOrderRequest(
        instrument_id=payload.instrument_id.upper(),
        side=payload.side.upper(),
        quantity=payload.quantity,
        order_type=payload.order_type.upper(),
        limit_price=payload.limit_price,
        trigger_price=payload.trigger_price,
    )
    try:
        result = _sandbox_broker().place_order(request)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "order_id": result.order_id,
        "status": result.status,
        "account_mode": "PAPER_SANDBOX",
        "real_money": False,
        "message": "Paper order created. It fills only when processed against an observed market bar.",
    }


@router.post("/paper-sandbox/orders/{order_id}/cancel")
def cancel_paper_sandbox_order(order_id: str):
    return _sandbox_broker().cancel_order(order_id)


@router.post("/paper-sandbox/process")
def process_paper_sandbox(payload: PaperSandboxProcessRequest):
    engine = _engine()
    trade_date = payload.trade_date
    if trade_date is None:
        with engine.connect() as conn:
            trade_date = conn.execute(text("select max(trade_date) from daily_prices")).scalar_one_or_none()
    if trade_date is None:
        raise HTTPException(status_code=400, detail="No market bars are available")
    broker = PaperBroker(engine, account_mode="PAPER_SANDBOX", broker_name="PAPER_SANDBOX")
    try:
        fills = PaperExecutionService(engine, broker=broker, account_mode="PAPER_SANDBOX").process_date(trade_date)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "trade_date": trade_date,
        "fills": fills,
        "fill_count": len(fills),
        "real_money": False,
        "formal_validation_evidence": False,
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


@router.post("/enable-orders")
def enable_orders():
    try:
        SafetyController(_engine()).enable_new_orders(actor="api_operator")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"new_orders_enabled": True}


@router.post("/disable-orders")
def disable_orders():
    SafetyController(_engine()).disable_new_orders(actor="api_operator")
    return {"new_orders_enabled": False}


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


@router.get("/audit")
def audit_history(entity_type: str | None = None, entity_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
    rows = AuditTrail(_engine()).history(entity_type=entity_type, entity_id=entity_id, limit=limit)
    return {"count": len(rows), "events": rows}


@router.post("/paper-validation")
def paper_validation(payload: PaperValidationRequest):
    result = PaperValidationService(_engine()).evaluate(payload.started_on, payload.ended_on, actor="api_operator")
    return {"run_id":result.run_id,"passed":result.passed,"metrics":result.metrics,"gates":result.gates,"criteria":result.criteria.__dict__}


@router.get("/paper-validation/latest")
def latest_paper_validation():
    with _engine().connect() as conn:
        row = conn.execute(text("select * from paper_validation_runs order by created_at desc limit 1")).mappings().first()
    return dict(row) if row else {"status":"never_run"}


@router.get("/readiness")
def readiness():
    ctl = ProductionReadinessController(_engine())
    result = ctl.evaluate()
    ctl.persist(result, actor="api_operator")
    return result.__dict__


@router.post("/promote/{stage}")
def promote(stage: str):
    try:
        result = ProductionReadinessController(_engine()).promote(stage.upper(), actor="api_operator")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"promoted_to": stage.upper(), "readiness": result.__dict__, "live_auto_enabled": False}


@router.post("/live-validation/session")
def create_live_validation_session(payload: LiveSessionRequest):
    limits = LiveValidationLimits(payload.max_capital,payload.max_order_value,payload.max_open_positions,payload.max_daily_loss,tuple(s.upper() for s in payload.allowed_symbols))
    try:
        session_id = LiveValidationService(_engine()).create_session(limits, actor="api_operator", notes=payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id":session_id,"status":"DRAFT"}


@router.post("/live-validation/session/{session_id}/activate")
def activate_live_validation_session(session_id: str):
    try:
        LiveValidationService(_engine()).activate_session(session_id, actor="api_operator")
    except (RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id":session_id,"status":"ACTIVE"}


@router.post("/live-validation/session/{session_id}/close")
def close_live_validation_session(session_id: str, completed: bool = True):
    try:
        LiveValidationService(_engine()).close_session(session_id, actor="api_operator", completed=completed)
    except (RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id":session_id,"status":"COMPLETED" if completed else "ABORTED"}


@router.post("/live-validation/session/{session_id}/proposal")
def create_live_validation_proposal(session_id: str, payload: LiveProposalRequest):
    request = BrokerOrderRequest(payload.instrument_id.upper(),payload.side.upper(),payload.quantity,payload.order_type.upper(),payload.limit_price,payload.trigger_price)
    try:
        approval_id = LiveValidationService(_engine()).propose_order(session_id,request,estimated_price=payload.estimated_price,actor="api_operator",reason=payload.reason)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"approval_id":approval_id,"status":"PROPOSED","broker_submission":False}


@router.post("/live-validation/proposal/{approval_id}/approve")
def approve_live_validation_proposal(approval_id: str):
    try:
        LiveValidationService(_engine()).approve(approval_id, actor="api_operator")
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"approval_id":approval_id,"status":"APPROVED","broker_submission":False}


@router.post("/live-validation/proposal/{approval_id}/reject")
def reject_live_validation_proposal(approval_id: str, payload: RejectRequest):
    LiveValidationService(_engine()).reject(approval_id, actor="api_operator", reason=payload.reason)
    return {"approval_id":approval_id,"status":"REJECTED"}


@router.post("/live-validation/proposal/{approval_id}/observation")
def record_live_validation_observation(approval_id: str, payload: LiveObservationRequest):
    try:
        observation_id = LiveValidationService(_engine()).record_observation(
            approval_id,
            expected_price=payload.expected_price,
            fill_price=payload.fill_price,
            quantity=payload.quantity,
            executed_at=payload.executed_at,
            reconciliation_ok=payload.reconciliation_ok,
            actor="api_operator",
            fees=payload.fees,
            external_order_reference=payload.external_order_reference,
            notes=payload.notes,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"observation_id":observation_id,"recorded":True}
