from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class SafetyStatus:
    execution_mode: str
    new_orders_enabled: bool
    emergency_stop: bool
    locked_reason: str | None

    @property
    def can_place_live_order(self) -> bool:
        return (
            self.execution_mode == "LIVE_APPROVAL"
            and self.new_orders_enabled
            and not self.emergency_stop
            and not self.locked_reason
        )


class SafetyController:
    def __init__(self, engine: Engine):
        self.engine = engine

    def status(self) -> SafetyStatus:
        with self.engine.connect() as conn:
            row = conn.execute(text("select execution_mode,new_orders_enabled,emergency_stop,locked_reason from safety_state where singleton=true")).mappings().one()
        return SafetyStatus(str(row["execution_mode"]), bool(row["new_orders_enabled"]), bool(row["emergency_stop"]), row["locked_reason"])

    def lock(self, reason: str, actor: str = "system") -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                update safety_state set new_orders_enabled=false, locked_reason=:reason,
                    updated_at=:now, updated_by=:actor where singleton=true
            """), {"reason": reason, "now": datetime.now(timezone.utc), "actor": actor})
            conn.execute(text("insert into audit_log(event_type,actor,payload) values ('EXECUTION_LOCKED',:actor,jsonb_build_object('reason',:reason))"), {"actor": actor, "reason": reason})

    def emergency_stop(self, reason: str, actor: str = "operator") -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                update safety_state set new_orders_enabled=false, emergency_stop=true,
                    locked_reason=:reason, updated_at=:now, updated_by=:actor where singleton=true
            """), {"reason": reason, "now": datetime.now(timezone.utc), "actor": actor})
            conn.execute(text("insert into audit_log(event_type,actor,payload) values ('EMERGENCY_STOP',:actor,jsonb_build_object('reason',:reason))"), {"actor": actor, "reason": reason})

    def set_mode(self, mode: str, actor: str = "operator") -> None:
        allowed = {"PAPER", "LIVE_APPROVAL"}
        if mode not in allowed:
            raise ValueError("Only PAPER and LIVE_APPROVAL are supported; LIVE_AUTO remains disabled")
        with self.engine.begin() as conn:
            conn.execute(text("""
                update safety_state set execution_mode=:mode, new_orders_enabled=false,
                    emergency_stop=false, locked_reason=null, updated_at=:now, updated_by=:actor
                where singleton=true
            """), {"mode": mode, "now": datetime.now(timezone.utc), "actor": actor})

    def enable_new_orders(self, actor: str = "operator") -> None:
        status = self.status()
        if status.emergency_stop or status.locked_reason:
            raise RuntimeError("Execution is locked; clear the cause before enabling new orders")
        if status.execution_mode not in {"PAPER", "LIVE_APPROVAL"}:
            raise RuntimeError("Unsupported execution mode")
        with self.engine.begin() as conn:
            conn.execute(text("update safety_state set new_orders_enabled=true,updated_at=:now,updated_by=:actor where singleton=true"), {"now": datetime.now(timezone.utc), "actor": actor})
