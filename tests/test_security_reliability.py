import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, event

from apps.api.main_exec import app
from apps.api import execution_routes
from apps.api.security import APISecurityMiddleware, MAX_BODY
from packages.pms.safety import SafetyController
from packages.pms.brokers import PaperBroker, BrokerOrderRequest
from packages.pms.reliability import runtime_posture, worker_health, safe_details
from packages.pms.recovery import (
    archive_sha,
    endpoint_fingerprint,
    inspect_archive,
    restore_rehearsal,
)
from packages.pms.live_validation import LiveValidationService, LiveValidationLimits
from test_matrix_data_api import Engine, Result


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "test-only-operator-key")
    return TestClient(app)


def test_auth_cannot_be_bypassed_by_missing_oversized_or_unicode_keys(
    client, monkeypatch
):
    for key in ("", "bad", "x" * 257):
        response = client.post(
            "/v1/execution/disable-orders", headers={"x-ops-key": key}
        )
        assert response.status_code == 401
    monkeypatch.delenv("OPS_API_KEY")
    assert (
        client.post(
            "/v1/execution/disable-orders",
            headers={"x-ops-key": "test-only-operator-key"},
        ).status_code
        == 401
    )


def test_security_headers_request_ids_and_no_input_echo(client):
    response = client.get("/health", headers={"x-request-id": "attacker-provided"})
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["x-frame-options"] == "DENY"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"] != "attacker-provided"
    response = client.post(
        "/v1/execution/paper-sandbox/orders",
        headers={"x-ops-key": "test-only-operator-key"},
        json={"quantity": "my-private-password"},
    )
    assert (
        response.status_code == 422
        and "my-private-password" not in response.text
        and "input" not in response.text
    )


def test_body_limits_apply_before_a_mutation_and_public_calculations_remain_public(
    client,
):
    response = client.post(
        "/v1/execution/disable-orders",
        headers={
            "x-ops-key": "test-only-operator-key",
            "content-length": str(MAX_BODY + 1),
        },
        content=b"{}",
    )
    assert response.status_code == 413
    response = client.post(
        "/v1/risk/position-size",
        json={"capital": 100000, "entry": 100, "signal_low": 96},
    )
    assert response.status_code == 200
    assert client.post("/v1/research/data-integrity").status_code == 405


def test_chunked_limit_and_unicode_auth_never_reach_handler(monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "ascii-key")
    called = []
    messages = []

    async def downstream(*a):
        called.append(True)

    middleware = APISecurityMiddleware(downstream)

    async def send(message):
        messages.append(message)

    async def receive():
        return {
            "type": "http.request",
            "body": b"x" * (MAX_BODY + 1),
            "more_body": False,
        }

    scope = dict(
        type="http",
        method="POST",
        path="/v1/execution/test",
        headers=[(b"x-ops-key", b"ascii-key")],
        app=SimpleNamespace(routes=[]),
    )
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 413 and not called
    messages.clear()
    scope["headers"] = [(b"x-ops-key", b"\xff")]
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 401


def test_unexpected_and_http_errors_cannot_expose_sql_or_credentials(
    monkeypatch, caplog
):
    service = FastAPI()
    service.add_middleware(APISecurityMiddleware)

    @service.get("/broken")
    def broken():
        raise RuntimeError("postgresql://user:private-password@db-host")

    c = TestClient(service)
    response = c.get("/broken")
    assert response.status_code == 500 and "private-password" not in response.text
    assert "private-password" not in caplog.text
    from apps.api import data_gap_routes

    monkeypatch.setattr(
        data_gap_routes,
        "_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("private-password")),
    )
    monkeypatch.setenv("OPS_API_KEY", "test-only-operator-key")
    response = TestClient(app).post(
        "/v1/execution/data-gaps/shareholding/backfill",
        json={},
        headers={"x-ops-key": "test-only-operator-key"},
    )
    assert response.status_code == 500 and "private-password" not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": True},
        {"quantity": 1.5},
        {"quantity": 0},
        {"limit_price": float("inf")},
        {"idempotency_key": ""},
        {"side": "INVALID"},
        {"order_type": "LIMIT", "limit_price": None},
        {"order_type": "STOP_MARKET", "trigger_price": None},
        {"instrument_id": "NSE:BAD;DROP"},
    ],
)
def test_order_input_validation_before_db(changes):
    payload = dict(
        instrument_id="NSE:TEST", side="BUY", quantity=1, idempotency_key="retry:fixed"
    )
    payload.update(changes)
    with pytest.raises(ValueError):
        execution_routes.PaperSandboxOrderRequest(**payload)


def safety_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def funcs(dbapi, *a):
        dbapi.create_function("jsonb_build_object", 2, lambda k, v: json.dumps({k: v}))

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table safety_state(singleton boolean primary key,execution_mode text,new_orders_enabled boolean,emergency_stop boolean,locked_reason text,updated_at text,updated_by text)"
            )
        )
        conn.execute(
            text(
                "insert into safety_state values(true,'PAPER',false,false,null,null,null)"
            )
        )
        conn.execute(
            text("create table audit_log(event_type text,actor text,payload text)")
        )
    return engine


def test_mode_switch_never_clears_a_stop_or_existing_lock():
    engine = safety_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "update safety_state set emergency_stop=true,locked_reason='source_not_ready'"
            )
        )
    safety = SafetyController(engine)
    safety.set_mode("PAPER")
    status = safety.status()
    assert (
        status.emergency_stop
        and status.locked_reason == "source_not_ready"
        and not status.new_orders_enabled
    )
    with pytest.raises(RuntimeError):
        safety.enable_new_orders()
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from audit_log")).scalar_one() == 1


def test_enable_is_atomic_and_disable_and_mode_are_audited(monkeypatch):
    engine = safety_engine()
    safety = SafetyController(engine)
    monkeypatch.setattr(
        safety,
        "status",
        lambda: pytest.fail("Separate precheck would introduce a race"),
    )
    safety.enable_new_orders()
    safety.disable_new_orders()
    safety.set_mode("PAPER")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select new_orders_enabled from safety_state")
            ).scalar_one()
            == 0
        )
        assert [
            r[0] for r in conn.execute(text("select event_type from audit_log"))
        ] == ["NEW_ORDERS_ENABLED", "NEW_ORDERS_DISABLED", "EXECUTION_MODE_SET"]
    with engine.begin() as conn:
        conn.execute(text("update safety_state set locked_reason='concurrent_stop'"))
    with pytest.raises(RuntimeError):
        safety.enable_new_orders()
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select new_orders_enabled from safety_state")
            ).scalar_one()
            == 0
        )


def test_safety_missing_singleton_and_auto_mode_fail_closed():
    engine = safety_engine()
    safety = SafetyController(engine)
    with engine.begin() as conn:
        conn.execute(text("delete from safety_state"))
    for action in (
        lambda: safety.lock("test"),
        lambda: safety.emergency_stop("test"),
        safety.enable_new_orders,
        safety.disable_new_orders,
        lambda: safety.set_mode("PAPER"),
    ):
        with pytest.raises(RuntimeError):
            action()
    with pytest.raises(ValueError):
        safety.set_mode("LIVE_AUTO")


def test_mode_api_uses_readiness_and_cannot_bypass_it(client, monkeypatch):
    seen = []

    class Readiness:
        def __init__(self, *a):
            pass

        def promote(self, mode, **kw):
            seen.append(mode)
            raise RuntimeError("readiness blocked")

    monkeypatch.setattr(execution_routes, "_engine", lambda: None)
    monkeypatch.setattr(execution_routes, "ProductionReadinessController", Readiness)
    r = client.post(
        "/v1/execution/mode",
        json={"mode": "LIVE_VALIDATION"},
        headers={"x-ops-key": "test-only-operator-key"},
    )
    assert r.status_code == 400 and seen == ["LIVE_VALIDATION"]


def broker_engine(existing):
    engine = Engine([None, existing])
    broker = PaperBroker(engine, account_mode="PAPER_SANDBOX")
    broker.lookup.row = lambda *a: dict(id=1)
    return broker


def existing_order():
    return dict(
        id="saved",
        mode="PAPER_SANDBOX",
        broker="PAPER_SANDBOX",
        instrument_id=1,
        side="BUY",
        quantity=10,
        order_type="LIMIT",
        limit_price=100,
        trigger_price=None,
        status="OPEN",
    )


def test_retry_token_matches_entire_order_economics():
    request = BrokerOrderRequest(
        "NSE:TEST", "BUY", 10, "LIMIT", 100, idempotency_key="stable"
    )
    assert broker_engine(existing_order()).place_order(request).order_id == "saved"
    for changed in (
        replace(request, limit_price=101),
        replace(request, order_type="MARKET"),
        replace(request, side="SELL"),
        replace(request, quantity=11),
    ):
        with pytest.raises(ValueError, match="Idempotency"):
            broker_engine(existing_order()).place_order(changed)


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": True},
        {"quantity": 1.5},
        {"limit_price": float("nan")},
        {"limit_price": -1},
    ],
)
def test_internal_broker_rejects_nonfinite_or_fractional_input(change):
    broker = PaperBroker(None)
    request = replace(BrokerOrderRequest("NSE:TEST", "BUY", 1), **change)
    with pytest.raises(ValueError):
        broker.place_order(request)


def test_online_connections_bound_waits_and_enforce_remote_tls(monkeypatch):
    import packages.pms.database as module

    seen = []
    monkeypatch.setattr(
        module, "create_engine", lambda url, **kw: seen.append((url, kw))
    )
    module.online_engine("postgresql://user:private@db.example/pms")
    args = seen[-1][1]
    assert args["connect_args"]["sslmode"] == "require" and args["pool_timeout"] == 5
    assert (
        "statement_timeout=30000" in args["connect_args"]["options"]
        and args["max_overflow"] == 3
    )
    module.online_engine("postgresql://user:private@localhost/pms", worker=True)
    assert seen[-1][1]["connect_args"].get("sslmode") is None
    with pytest.raises(ValueError):
        module.online_engine("postgresql://user:private@db.example/pms?sslmode=disable")


def test_readiness_probe_reports_database_failure_without_secret(client, monkeypatch):
    from apps.api import main

    monkeypatch.setattr(
        main,
        "db_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("private-password")),
    )
    r = client.get("/ready")
    assert r.status_code == 503 and "private-password" not in r.text
    assert client.get("/health").status_code == 200


def test_worker_health_never_fabricates_a_heartbeat_or_backup_pass(monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "private-test-key")
    now = datetime.now(timezone.utc)
    assert worker_health([], now)["status"] == "NO_EVIDENCE"
    rows = [
        dict(id="stale", status="RUNNING", started_at=now - timedelta(hours=2)),
        dict(id="partial", status="PARTIAL", started_at=now),
    ]
    h = worker_health(rows, now)
    assert (
        h["status"] == "ATTENTION"
        and h["stale_running_ids"] == ["stale"]
        and h["failed_or_partial_ids"] == ["partial"]
    )
    p = runtime_posture()
    assert p["operator_key_configured"] and p["operator_key_strength_review"]
    assert (
        p["backup_restore_evidence"] == "NOT_VERIFIED" and not p["execution_readiness"]
    )
    assert "private-test-key" not in json.dumps(p)


def test_historical_errors_and_database_urls_are_not_returned_in_journal_details():
    original = dict(
        error="private-password",
        nested=[
            dict(
                ops_api_key="private-key",
                reason="failed postgres://user:private@host/db",
            )
        ],
    )
    output = safe_details(original)
    assert (
        "private" not in json.dumps(output) and original["error"] == "private-password"
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1])
def test_live_records_cannot_bypass_limits_with_nonfinite_numbers(invalid):
    svc = LiveValidationService(None)
    with pytest.raises(ValueError):
        svc.create_session(LiveValidationLimits(invalid, 1, 1, 1), actor="test")
    with pytest.raises(ValueError):
        svc.propose_order(
            "id", BrokerOrderRequest("NSE:TEST", "BUY", 1), estimated_price=invalid
        )
    with pytest.raises(ValueError):
        svc.record_observation(
            "id",
            expected_price=invalid,
            fill_price=10,
            quantity=1,
            executed_at=datetime.now(timezone.utc),
            reconciliation_ok=True,
            actor="test",
        )


def test_archive_sha_and_inspection_do_not_prove_restore(monkeypatch, tmp_path):
    import packages.pms.recovery as module

    path = tmp_path / "backup.dump"
    path.write_bytes(b"trusted fixture")
    digest = archive_sha(path)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout=b";comment\n1; table entry\n"
        ),
    )
    r = inspect_archive(path, digest)
    assert r["toc_entries"] == 1 and not r["restored"]
    with pytest.raises(ValueError):
        inspect_archive(path, "0" * 64)
    assert endpoint_fingerprint(
        "postgresql://u:password@host/db"
    ) == endpoint_fingerprint("postgresql://u:other@host/db")


def test_restore_refuses_source_or_nonempty_target_and_requires_manifest_match(
    monkeypatch, tmp_path
):
    import packages.pms.recovery as module

    path = tmp_path / "backup.dump"
    path.write_bytes(b"trusted fixture")
    digest = archive_sha(path)
    calls = []

    def command(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(returncode=0, stdout=b"1; table entry\n")

    monkeypatch.setattr(module.subprocess, "run", command)
    source = "postgresql://u:private-source@source/db"
    target = "postgresql://u:private-target@target/scratch"
    with pytest.raises(ValueError, match="isolated"):
        restore_rehearsal(
            path, digest, source, source, Engine([]), verify=lambda *a: True
        )
    with pytest.raises(ValueError, match="objects"):
        restore_rehearsal(
            path, digest, source, target, Engine([1]), verify=lambda *a: True
        )
    assert not any("--dbname" in args for args, _ in calls)
    with pytest.raises(ValueError, match="manifest"):
        restore_rehearsal(
            path, digest, source, target, Engine([0]), verify=lambda *a: False
        )
    r = restore_rehearsal(
        path, digest, source, target, Engine([0]), verify=lambda *a: True
    )
    assert (
        r["status"] == "RESTORE_MANIFEST_MATCHED" and r["acceptance"] == "NOT_CERTIFIED"
    )
    args, kw = calls[-1]
    assert "private" not in str(args) and kw["env"]["PGPASSWORD"] == "private-target"
    assert "--single-transaction" in args and "--clean" not in args


def test_read_only_deployment_blocks_operational_mutations_but_keeps_calculations(client, monkeypatch):
    monkeypatch.setenv('PMS_API_READ_ONLY','1')
    response = client.post('/v1/execution/disable-orders',headers={'x-ops-key':'test-only-operator-key'})
    assert response.status_code==503 and 'read-only' in response.json()['detail']
    assert client.get('/health').status_code==200
    response = client.post('/v1/risk/position-size',json={'capital':100000,'entry':100,'signal_low':96})
    assert response.status_code==200
