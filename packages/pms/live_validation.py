from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from packages.pms.audit import AuditEvent, AuditTrail
from packages.pms.brokers import BrokerOrderRequest
from packages.pms.safety import SafetyController


@dataclass(frozen=True)
class LiveValidationLimits:
    max_capital: float
    max_order_value: float
    max_open_positions: int
    max_daily_loss: float
    allowed_symbols: tuple[str, ...] = ()


class LiveValidationService:
    """Creates tightly bounded live-validation proposals.

    This service never sends an order to a broker. Approval records are an
    operator decision artifact that can be reviewed before any separate manual
    broker action is taken.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self.audit = AuditTrail(engine)
        self.safety = SafetyController(engine)

    def create_session(self, limits: LiveValidationLimits, *, actor: str, notes: str | None = None) -> str:
        if min(limits.max_capital, limits.max_order_value, limits.max_daily_loss) <= 0 or limits.max_open_positions <= 0:
            raise ValueError("live-validation limits must be positive")
        if limits.max_order_value > limits.max_capital:
            raise ValueError("max_order_value cannot exceed max_capital")
        session_id = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text("""
                insert into live_validation_sessions
                (id,status,max_capital,max_order_value,max_open_positions,max_daily_loss,allowed_symbols,notes,created_by)
                values (:id,'DRAFT',:capital,:order_value,:positions,:daily_loss,cast(:symbols as jsonb),:notes,:actor)
            """), {"id":session_id,"capital":limits.max_capital,"order_value":limits.max_order_value,"positions":limits.max_open_positions,"daily_loss":limits.max_daily_loss,"symbols":json.dumps(list(limits.allowed_symbols)),"notes":notes,"actor":actor})
        self.audit.record(AuditEvent("LIVE_VALIDATION_SESSION_CREATED", actor, {"limits":limits.__dict__}, entity_type="live_validation_session", entity_id=session_id))
        return session_id

    def activate_session(self, session_id: str, *, actor: str) -> None:
        status = self.safety.status()
        if status.execution_mode != "LIVE_VALIDATION":
            raise RuntimeError("Safety mode must be LIVE_VALIDATION before activation")
        if status.emergency_stop or status.locked_reason:
            raise RuntimeError("Execution safety state is locked")
        with self.engine.begin() as conn:
            active = conn.execute(text("select count(*) from live_validation_sessions where status='ACTIVE' and id<>:id"), {"id":session_id}).scalar_one()
            if active:
                raise RuntimeError("another live-validation session is already active")
            updated = conn.execute(text("update live_validation_sessions set status='ACTIVE',started_at=:now,activated_by=:actor where id=:id and status='DRAFT'"), {"id":session_id,"now":datetime.now(timezone.utc),"actor":actor}).rowcount
            if not updated:
                raise RuntimeError("session is not in DRAFT state")
        self.audit.record(AuditEvent("LIVE_VALIDATION_SESSION_ACTIVATED", actor, {}, entity_type="live_validation_session", entity_id=session_id))

    def _session(self, session_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(text("select * from live_validation_sessions where id=:id"), {"id":session_id}).mappings().first()
        if not row:
            raise KeyError(session_id)
        return dict(row)

    def propose_order(self, session_id: str, request: BrokerOrderRequest, *, estimated_price: float, actor: str = "strategy", reason: str | None = None) -> str:
        session = self._session(session_id)
        if session["status"] != "ACTIVE":
            raise RuntimeError("live-validation session is not active")
        symbol = request.instrument_id.upper()
        allowed = tuple(session.get("allowed_symbols") or [])
        estimated_value = float(estimated_price) * int(request.quantity)
        if estimated_value <= 0:
            raise ValueError("estimated order value must be positive")
        if estimated_value > float(session["max_order_value"]):
            raise RuntimeError("order exceeds live-validation max_order_value")
        if allowed and symbol not in {str(x).upper() for x in allowed}:
            raise RuntimeError("instrument is outside the live-validation allowlist")
        with self.engine.connect() as conn:
            committed = float(conn.execute(text("""
                select coalesce(sum(estimated_order_value),0) from live_order_approvals
                where session_id=:session and status in ('PROPOSED','APPROVED') and side='BUY'
            """), {"session":session_id}).scalar_one())
            if request.side.upper() == "BUY" and committed + estimated_value > float(session["max_capital"]):
                raise RuntimeError("session committed notional would exceed max_capital")
            latest_live = conn.execute(text("""
                select realized_pnl,unrealized_pnl from portfolio_snapshots
                where account_mode in ('LIVE_VALIDATION','LIVE_APPROVAL') and snapshot_date=:d
                order by created_at desc limit 1
            """), {"d":date.today()}).mappings().first()
            if latest_live and float(latest_live["realized_pnl"] or 0) + float(latest_live["unrealized_pnl"] or 0) <= -float(session["max_daily_loss"]):
                raise RuntimeError("live-validation max_daily_loss reached")
            exchange, raw_symbol = symbol.split(":", 1)
            trading_symbol = f"{raw_symbol}-EQ" if exchange == "NSE" else raw_symbol
            instrument = conn.execute(text("select id from instruments where exchange=:e and trading_symbol=:s order by valid_from desc limit 1"), {"e":exchange,"s":trading_symbol}).scalar_one_or_none()
            if instrument is None:
                raise KeyError(symbol)
            open_positions = int(conn.execute(text("select count(*) from trades where state not in ('CLOSED','CANCELLED')")).scalar_one())
            if request.side.upper() == "BUY" and open_positions >= int(session["max_open_positions"]):
                raise RuntimeError("live-validation max_open_positions reached")
        approval_id = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text("""
                insert into live_order_approvals
                (id,session_id,instrument_id,side,quantity,order_type,limit_price,trigger_price,estimated_order_value,proposal_reason,proposed_by)
                values (:id,:session,:instrument,:side,:qty,:type,:limit,:trigger,:value,:reason,:actor)
            """), {"id":approval_id,"session":session_id,"instrument":instrument,"side":request.side.upper(),"qty":request.quantity,"type":request.order_type.upper(),"limit":request.limit_price,"trigger":request.trigger_price,"value":estimated_value,"reason":reason,"actor":actor})
        self.audit.record(AuditEvent("LIVE_ORDER_PROPOSED", actor, {"instrument":symbol,"quantity":request.quantity,"estimated_order_value":estimated_value}, entity_type="live_order_approval", entity_id=approval_id))
        return approval_id

    def approve(self, approval_id: str, *, actor: str) -> None:
        status = self.safety.status()
        if status.execution_mode != "LIVE_VALIDATION" or status.emergency_stop or status.locked_reason:
            raise PermissionError("live-validation approval is blocked by safety state")
        with self.engine.begin() as conn:
            updated = conn.execute(text("""
                update live_order_approvals set status='APPROVED',approved_at=:now,approved_by=:actor
                where id=:id and status='PROPOSED'
            """), {"id":approval_id,"now":datetime.now(timezone.utc),"actor":actor}).rowcount
            if not updated:
                raise RuntimeError("approval is not in PROPOSED state")
        self.audit.record(AuditEvent("LIVE_ORDER_APPROVED", actor, {}, entity_type="live_order_approval", entity_id=approval_id))

    def reject(self, approval_id: str, *, actor: str, reason: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("update live_order_approvals set status='REJECTED',rejection_reason=:reason,approved_by=:actor,approved_at=:now where id=:id and status='PROPOSED'"), {"id":approval_id,"reason":reason,"actor":actor,"now":datetime.now(timezone.utc)})
        self.audit.record(AuditEvent("LIVE_ORDER_REJECTED", actor, {"reason":reason}, entity_type="live_order_approval", entity_id=approval_id))

    def close_session(self, session_id: str, *, actor: str, completed: bool, notes: str | None = None) -> None:
        session = self._session(session_id)
        if session["status"] != "ACTIVE":
            raise RuntimeError("only ACTIVE sessions can be closed")
        with self.engine.connect() as conn:
            unresolved = int(conn.execute(text("select count(*) from live_order_approvals where session_id=:id and status='PROPOSED'"), {"id":session_id}).scalar_one())
        if completed and unresolved:
            raise RuntimeError("cannot complete a session with unresolved order proposals")
        status = "COMPLETED" if completed else "ABORTED"
        with self.engine.begin() as conn:
            conn.execute(text("update live_validation_sessions set status=:status,ended_at=:now,closed_by=:actor,notes=coalesce(:notes,notes) where id=:id"), {"status":status,"now":datetime.now(timezone.utc),"actor":actor,"notes":notes,"id":session_id})
        self.audit.record(AuditEvent("LIVE_VALIDATION_SESSION_CLOSED", actor, {"status":status,"unresolved_proposals":unresolved}, entity_type="live_validation_session", entity_id=session_id))
