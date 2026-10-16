from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text

from packages.pms.audit import AuditEvent, AuditTrail
from packages.pms.safety import SafetyController


@dataclass(frozen=True)
class ReadinessResult:
    stage: str
    readiness_status: str
    gates: dict[str, bool]
    details: dict[str, Any]


class ProductionReadinessController:
    """Controls PAPER -> LIVE_VALIDATION -> LIVE_APPROVAL promotion.

    LIVE_AUTO is intentionally not part of the state machine.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self.audit = AuditTrail(engine)
        self.safety = SafetyController(engine)

    def evaluate(self) -> ReadinessResult:
        safety = self.safety.status()
        with self.engine.connect() as conn:
            paper = conn.execute(text("select id,passed,created_at from paper_validation_runs order by created_at desc limit 1")).mappings().first()
            recon = conn.execute(text("select status,completed_at from reconciliation_runs order by started_at desc limit 1")).mappings().first()
            dq_errors = int(conn.execute(text("select count(*) from data_quality_issues where severity='ERROR'")).scalar_one())
            failed_workers = int(conn.execute(text("select count(*) from worker_runs where started_at >= now()-interval '7 days' and status='FAILED'")).scalar_one())
            live_session = conn.execute(text("select id,status,started_at,ended_at from live_validation_sessions order by created_at desc limit 1")).mappings().first()
        gates = {
            "paper_validation_passed": bool(paper and paper["passed"]),
            "latest_reconciliation_clean": bool(recon and recon["status"] == "SUCCESS"),
            "no_data_quality_errors": dq_errors == 0,
            "no_recent_worker_failures": failed_workers == 0,
            "safety_unlocked": not safety.emergency_stop and not safety.locked_reason,
        }
        live_completed = bool(live_session and live_session["status"] == "COMPLETED")
        details = {
            "latest_paper_validation_id": str(paper["id"]) if paper else None,
            "latest_live_validation_id": str(live_session["id"]) if live_session else None,
            "live_validation_completed": live_completed,
            "data_quality_errors": dq_errors,
            "recent_worker_failures": failed_workers,
            "execution_mode": safety.execution_mode,
        }
        if all(gates.values()) and live_completed:
            stage = "LIVE_APPROVAL"
            readiness = "READY_FOR_LIVE_APPROVAL"
        elif all(gates.values()):
            stage = "LIVE_VALIDATION"
            readiness = "READY_FOR_LIVE_VALIDATION"
        else:
            stage = "PAPER"
            readiness = "NOT_READY"
        return ReadinessResult(stage, readiness, gates, details)

    def persist(self, result: ReadinessResult, *, actor: str = "system") -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                update production_readiness
                set stage=:stage,readiness_status=:status,gates=cast(:gates as jsonb),
                    latest_paper_validation_id=:paper_id,latest_live_validation_id=:live_id,
                    updated_at=:now,updated_by=:actor where singleton=true
            """), {
                "stage": result.stage,
                "status": result.readiness_status,
                "gates": json.dumps(result.gates),
                "paper_id": result.details.get("latest_paper_validation_id"),
                "live_id": result.details.get("latest_live_validation_id"),
                "now": datetime.now(timezone.utc),
                "actor": actor,
            })
        self.audit.record(AuditEvent("PRODUCTION_READINESS_EVALUATED", actor, {"stage":result.stage,"readiness_status":result.readiness_status,"gates":result.gates,"details":result.details}, entity_type="production_readiness", entity_id="singleton"))

    def promote(self, target: str, *, actor: str) -> ReadinessResult:
        target = target.upper()
        if target == "LIVE_AUTO":
            raise ValueError("LIVE_AUTO remains disabled")
        result = self.evaluate()
        allowed = {"PAPER": True, "LIVE_VALIDATION": result.stage in {"LIVE_VALIDATION","LIVE_APPROVAL"}, "LIVE_APPROVAL": result.stage == "LIVE_APPROVAL"}
        if target not in allowed:
            raise ValueError("unsupported production stage")
        if not allowed[target]:
            raise RuntimeError(f"readiness gates do not allow promotion to {target}")
        self.safety.set_mode(target, actor=actor)
        self.persist(result, actor=actor)
        self.audit.record(AuditEvent("PRODUCTION_STAGE_PROMOTED", actor, {"target":target}, entity_type="production_readiness", entity_id="singleton"))
        return result
