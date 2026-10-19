from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json
import pytest
from sqlalchemy.exc import OperationalError

pytest.importorskip("celery")
from packages.pms import workers, worker_tasks_extended as extended
from packages.pms.reconciliation import ReconciliationService
from packages.pms.production_readiness import ProductionReadinessController
from packages.pms.safety import SafetyStatus
from packages.pms.live_validation import LiveValidationService
from packages.pms.brokers import BrokerOrderRequest
from test_matrix_data_api import Engine, Result


class DisposableEngine(Engine):
    disposed = False

    def dispose(self):
        self.disposed = True


def test_celery_only_accepts_json_and_bounds_consumption():
    c = workers.celery_app.conf
    assert (
        c.accept_content == ["json"]
        and c.task_serializer == "json"
        and c.result_serializer == "json"
    )
    assert (
        c.worker_prefetch_multiplier == 1 and c.task_time_limit > c.task_soft_time_limit
    )
    assert c.task_reject_on_worker_lost is False
    assert workers.market_close_pipeline.acks_late is True


def test_record_failure_disposes_connection_and_omits_raw_error(monkeypatch):
    engine = DisposableEngine([])

    def fail(*a, **kw):
        raise RuntimeError("private-password")

    engine.begin = fail
    monkeypatch.setattr(workers, "_engine", lambda: engine)
    with pytest.raises(RuntimeError, match="journal") as error:
        workers._record("test", "test")
    assert engine.disposed and "private-password" not in str(error.value)


def test_daily_retry_freezes_date_and_disposes_engine(monkeypatch):
    import packages.pms.daily_orchestrator as module

    engine = DisposableEngine([])
    monkeypatch.setattr(workers, "_engine", lambda: engine)

    class Daily:
        def __init__(self, *a):
            pass

        def run(self, d):
            raise OperationalError("private-password", None, None)

    monkeypatch.setattr(module, "DailyOrchestrator", Daily)

    class Retry(Exception):
        pass

    seen = []

    def retry(**kwargs):
        seen.append(kwargs)
        raise Retry()

    monkeypatch.setattr(workers.market_close_pipeline, "retry", retry)
    with pytest.raises(Retry):
        workers.market_close_pipeline.run("2026-10-19")
    assert engine.disposed and seen[0]["kwargs"] == {"trade_date": "2026-10-19"}
    assert (
        seen[0]["args"] == ()
        and seen[0]["max_retries"] == 3
        and "private-password" not in str(seen[0]["exc"])
    )


def test_blocked_data_is_not_blindly_retried(monkeypatch):
    import packages.pms.daily_orchestrator as module

    engine = DisposableEngine([])
    monkeypatch.setattr(workers, "_engine", lambda: engine)

    class Daily:
        def __init__(self, *a):
            pass

        def run(self, d):
            return {"status": "BLOCKED"}

    monkeypatch.setattr(module, "DailyOrchestrator", Daily)
    monkeypatch.setattr(
        workers.market_close_pipeline,
        "retry",
        lambda **kw: pytest.fail("Configuration/data blocker retried"),
    )
    with pytest.raises(RuntimeError):
        workers.market_close_pipeline.run("2026-10-19")
    assert engine.disposed


def test_financial_partial_failures_are_not_recorded_as_success_and_are_sanitized(
    monkeypatch,
):
    engine = DisposableEngine([])
    saved = []
    monkeypatch.setattr(extended, "_record", lambda *a: (engine, "id"))
    monkeypatch.setattr(extended, "_finish", lambda e, i, s, d: saved.append((s, d)))

    class Pipeline:
        def __init__(self, *a):
            pass

        def evaluate(self, symbol, *a, **kw):
            if symbol == "NSE:BAD":
                raise RuntimeError("postgres://u:private-password@host/db")
            return None, SimpleNamespace(eligible=False, score=0)

    monkeypatch.setattr(extended, "ModuleAPipeline", Pipeline)
    extended.financial_refresh.run(["NSE:GOOD", "NSE:BAD"], "2026-10-19")
    assert (
        engine.disposed
        and saved[0][0] == "PARTIAL"
        and "private-password" not in json.dumps(saved)
    )


def test_invalid_task_date_also_disposes_engine(monkeypatch):
    engine = DisposableEngine([])
    monkeypatch.setattr(extended, "_record", lambda *a: (engine, "id"))
    monkeypatch.setattr(extended, "_fail", lambda *a, **kw: None)
    with pytest.raises(RuntimeError):
        extended.research_snapshot.run("not-a-date")
    assert engine.disposed


def test_worker_reconciliation_never_uses_disabled_live_broker(monkeypatch):
    monkeypatch.setattr(
        workers, "_record", lambda *a: pytest.fail("Worker touched live journal")
    )
    with pytest.raises(ValueError):
        workers.reconcile.run("LIVE_APPROVAL")


def broker(**overrides):
    return SimpleNamespace(
        name="PAPER",
        account_mode="PAPER",
        initial_cash=1000,
        get_orders=lambda: [dict(id="order", status="FILLED")],
        get_positions=lambda: [dict(symbol="TEST", quantity=1)],
        get_cash=lambda: 900,
        **overrides
    )


def test_reconciliation_matches_internal_order_position_cash_evidence():
    engine = Engine(
        [
            None,
            [dict(id="order", status="FILLED", external_order_id=None)],
            [dict(symbol="TEST", quantity=1)],
            -100,
            None,
        ]
    )
    r = ReconciliationService(engine, broker()).run()
    assert r.ok and engine.calls[-1][1]["s"] == "MATCHED"


def test_missing_order_produces_mismatch_and_lock(monkeypatch):
    b = broker()
    b.get_orders = lambda: []
    engine = Engine(
        [
            None,
            [dict(id="order", status="FILLED", external_order_id=None)],
            [dict(symbol="TEST", quantity=1)],
            -100,
            None,
        ]
    )
    service = ReconciliationService(engine, b)
    locks = []
    monkeypatch.setattr(service.safety, "lock", lambda x: locks.append(x))
    r = service.run()
    assert not r.ok and locks == ["broker_reconciliation_mismatch"]


def test_reconciliation_exception_records_failed_and_locks_without_leaking(monkeypatch):
    b = broker()
    b.get_orders = lambda: (_ for _ in ()).throw(RuntimeError("private-password"))
    engine = Engine([None, None])
    service = ReconciliationService(engine, b)
    locks = []
    monkeypatch.setattr(service.safety, "lock", lambda x: locks.append(x))
    with pytest.raises(RuntimeError) as error:
        service.run()
    assert "private-password" not in str(error.value)
    assert (
        "status='FAILED'" in engine.calls[-1][0]
        and "private-password" not in engine.calls[-1][1]["details"]
    )
    assert locks


def test_paper_broker_cannot_fabricate_live_reconciliation():
    with pytest.raises(ValueError):
        ReconciliationService(None, broker()).run("LIVE_APPROVAL")


@pytest.mark.parametrize("flag", [True, False])
def test_readiness_uses_only_formal_paper_and_matched_flags(flag, monkeypatch):
    now = datetime.now(timezone.utc)
    engine = Engine(
        [
            dict(id="p", passed=True, created_at=now),
            dict(
                status="MATCHED",
                completed_at=now,
                orders_match=flag,
                positions_match=True,
                cash_match=True,
            ),
            0,
            0,
            None,
        ]
    )
    controller = ProductionReadinessController(engine)
    monkeypatch.setattr(
        controller.safety, "status", lambda: SafetyStatus("PAPER", False, False, None)
    )
    result = controller.evaluate()
    assert result.gates["latest_reconciliation_clean"] is flag
    assert "account_mode='PAPER'" in engine.calls[1][0]


def test_live_proposal_uses_one_transaction_and_locks_session_before_budget_read(
    monkeypatch,
):
    monkeypatch.setattr(Result, "scalar_one_or_none", Result.scalar_one, raising=False)
    monkeypatch.setattr(Result, "one", Result.scalar_one, raising=False)
    session = dict(
        status="ACTIVE",
        max_order_value=1000,
        max_capital=2000,
        max_open_positions=2,
        max_daily_loss=100,
        allowed_symbols=[],
    )
    engine = Engine([session, 0, None, 1, 0, None])
    svc = LiveValidationService(engine)
    monkeypatch.setattr(svc.audit, "record", lambda *a: None)
    svc.propose_order(
        "session", BrokerOrderRequest("NSE:TEST", "BUY", 1), estimated_price=100
    )
    assert engine.closed == 1 and "for update" in engine.calls[0][0]
    assert "sum(estimated_order_value)" in engine.calls[1][0]
    assert "insert into live_order_approvals" in engine.calls[-1][0]


def test_live_observations_cannot_reuse_full_quantity_or_future_timestamps():
    svc = LiveValidationService(Engine([dict(status="APPROVED", quantity=10), 10]))
    with pytest.raises(RuntimeError, match="Cumulative"):
        svc.record_observation(
            "id",
            expected_price=100,
            fill_price=100,
            quantity=1,
            executed_at=datetime.now(timezone.utc),
            reconciliation_ok=True,
            actor="test",
        )
    svc = LiveValidationService(None)
    with pytest.raises(ValueError, match="historical"):
        svc.record_observation(
            "id",
            expected_price=100,
            fill_price=100,
            quantity=1,
            executed_at=datetime.now(timezone.utc) + timedelta(days=1),
            reconciliation_ok=True,
            actor="test",
        )


@pytest.mark.parametrize("stop,active", [(True, "session"), (False, None)])
def test_approval_rechecks_safety_and_session_under_lock(monkeypatch, stop, active):
    monkeypatch.setattr(Result, "scalar_one_or_none", Result.scalar_one, raising=False)
    monkeypatch.setattr(Result, "one", Result.scalar_one, raising=False)
    current = dict(
        execution_mode="LIVE_VALIDATION",
        new_orders_enabled=True,
        emergency_stop=stop,
        locked_reason=None,
    )
    engine = Engine([current, active])
    svc = LiveValidationService(engine)
    monkeypatch.setattr(
        svc.safety, "status", lambda: SafetyStatus("LIVE_VALIDATION", True, False, None)
    )
    with pytest.raises((PermissionError, RuntimeError)):
        svc.approve("approval", actor="test")
    assert "for update" in engine.calls[0][0] and not any(
        "update live_order_approvals" in q for q, _ in engine.calls
    )


def test_approval_serializes_safety_session_and_record_updates(monkeypatch):
    monkeypatch.setattr(Result, "scalar_one_or_none", Result.scalar_one, raising=False)
    monkeypatch.setattr(Result, "one", Result.scalar_one, raising=False)
    current = dict(
        execution_mode="LIVE_VALIDATION",
        new_orders_enabled=True,
        emergency_stop=False,
        locked_reason=None,
    )
    engine = Engine([current, "session", None])
    svc = LiveValidationService(engine)
    monkeypatch.setattr(
        svc.safety, "status", lambda: SafetyStatus("LIVE_VALIDATION", True, False, None)
    )
    monkeypatch.setattr(svc.audit, "record", lambda *a: None)
    svc.approve("approval", actor="test")
    assert (
        "for update" in engine.calls[0][0] and "for update of s" in engine.calls[1][0]
    )
    assert "update live_order_approvals" in engine.calls[2][0] and engine.closed == 1
