from __future__ import annotations

import os
from datetime import date, datetime, timezone
from uuid import uuid4

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.pms.module_c_pipeline import ModuleCPipeline
from packages.pms.module_e_pipeline import ModuleEPipeline
from packages.pms.portfolio_accounting import PortfolioAccountingService
from packages.pms.brokers import PaperBroker, AngelOneBroker
from packages.pms.reconciliation import ReconciliationService
from packages.pms.safety import SafetyController

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("pms_workers", broker=REDIS_URL, backend=REDIS_URL, include=["packages.pms.worker_tasks_extended"])
celery_app.conf.timezone = "Asia/Kolkata"
celery_app.conf.task_track_started = True
celery_app.conf.beat_schedule = {
    "market-close-pipeline": {"task": "pms.market_close", "schedule": crontab(hour=18, minute=15, day_of_week="1-5")},
    "paper-reconciliation": {"task": "pms.reconcile", "schedule": crontab(hour=18, minute=30, day_of_week="1-5"), "args": ("PAPER",)},
    "data-health-check": {"task": "pms.data_health", "schedule": crontab(hour=18, minute=40, day_of_week="1-5")},
    "surveillance-guard": {"task": "pms.surveillance_guard", "schedule": crontab(hour=18, minute=45, day_of_week="1-5")},
    "research-snapshot": {"task": "pms.research_snapshot", "schedule": crontab(hour=18, minute=50, day_of_week="1-5")},
    "readiness-refresh": {"task": "pms.readiness_refresh", "schedule": crontab(hour=19, minute=0, day_of_week="1-5")},
    "weekly-paper-validation": {"task": "pms.paper_validation", "schedule": crontab(hour=19, minute=10, day_of_week="5"), "args": (60,)},
}


def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for workers")
    return create_engine(_render_safe_database_url(url), pool_pre_ping=True)


def _record(worker: str, job: str):
    engine = _engine()
    run_id = str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("insert into worker_runs(id,worker_name,job_name,status) values (:id,:w,:j,'RUNNING')"),
            {"id": run_id, "w": worker, "j": job},
        )
    return engine, run_id


def _finish(engine, run_id: str, status: str, details: dict):
    import json

    with engine.begin() as conn:
        conn.execute(
            text("update worker_runs set status=:s,finished_at=:t,details=cast(:d as jsonb) where id=:id"),
            {
                "s": status,
                "t": datetime.now(timezone.utc),
                "d": json.dumps(details, default=str),
                "id": run_id,
            },
        )


@celery_app.task(name="pms.market_close")
def market_close_pipeline(trade_date: str | None = None):
    """Run the PAPER after-market-close decision pipeline.

    Ordering is deliberate: Module E capacity must exist before Module C allocation.
    No broker submission is performed here.
    """
    engine, run_id = _record("market_worker", "market_close_pipeline")
    d = date.fromisoformat(trade_date) if trade_date else date.today()
    try:
        regime_pipeline=ModuleEPipeline(engine)
        market_date=regime_pipeline.resolve_session(d)[2]
        accounting = PortfolioAccountingService(engine)
        snap = accounting.snapshot(market_date, "PAPER")
        accounting.persist_snapshot(snap)
        _, regime = regime_pipeline.evaluate(d, persist=True)
        module_c = ModuleCPipeline(engine).generate(d)

        payload = {
            "trade_date": str(d),
            "regime": regime.band,
            "capacity": regime.max_open_trades,
            "regime_data_ready":regime.data_ready,
            "regime_reason":regime.reason,
            "market_data_session":str(market_date),
            "module_c": {
                "universe_size": module_c.universe_size,
                "a_eligible": module_c.a_eligible,
                "b_signals": module_c.b_signals,
                "ranked": module_c.ranked,
                "qualified": module_c.qualified,
                "selected": module_c.selected,
                "rejected": module_c.rejected,
            },
            "paper_nav": snap.nav,
            "broker_orders_submitted": 0,
        }
        _finish(engine, run_id, "SUCCESS", payload)
        return payload
    except Exception as exc:
        _finish(engine, run_id, "FAILED", {"error": str(exc)})
        raise


@celery_app.task(name="pms.reconcile")
def reconcile(account_mode: str = "PAPER"):
    engine, run_id = _record("execution_worker", "reconcile")
    try:
        broker = PaperBroker(engine) if account_mode == "PAPER" else AngelOneBroker(engine)
        result = ReconciliationService(engine, broker).run(account_mode)
        payload = {
            "reconciliation_id": result.run_id,
            "ok": result.ok,
            "discrepancies": list(result.discrepancies),
        }
        _finish(engine, run_id, "SUCCESS" if result.ok else "MISMATCH", payload)
        return payload
    except Exception as exc:
        SafetyController(engine).lock(f"reconciliation_error:{type(exc).__name__}")
        _finish(engine, run_id, "FAILED", {"error": str(exc)})
        raise


@celery_app.task(name="pms.data_health")
def data_health_check():
    engine, run_id = _record("market_worker", "data_health")
    try:
        with engine.connect() as conn:
            latest_price = conn.execute(text("select max(trade_date) from daily_prices")).scalar_one_or_none()
            errors = int(
                conn.execute(
                    text("select count(*) from data_quality_issues where severity='ERROR' and resolved_at is null")
                ).scalar_one()
            )
        stale = latest_price is None or (date.today() - latest_price).days > 5
        if stale or errors:
            SafetyController(engine).lock("data_health_failed")
        payload = {"latest_price": str(latest_price) if latest_price else None, "errors": errors, "stale": stale}
        _finish(engine, run_id, "SUCCESS" if not stale and not errors else "DEGRADED", payload)
        return payload
    except Exception as exc:
        SafetyController(engine).lock("data_health_exception")
        _finish(engine, run_id, "FAILED", {"error": str(exc)})
        raise
