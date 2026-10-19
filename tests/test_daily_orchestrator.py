from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from packages.pms.daily_orchestrator import DailyOrchestrator, DailyServices, RunJournal
from packages.pms.module_a_context import IST
from packages.pms.config import load_strategy_config
from packages.pms.market_calendar import MarketCalendar

DAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 19, tzinfo=IST)


class Journal:
    def __init__(self):
        self.rows = []
        self.acquired = True
        self.writes = []

    @contextmanager
    def exclusive(self):
        yield self.acquired

    def latest(self, key):
        rows = [r for r in self.rows if r["details"]["run_key"] == key]
        return deepcopy(rows[-1]) if rows else None

    def save(self, report):
        value = deepcopy(report)
        self.writes.append(value)
        row = dict(id=value["run_id"], status=value["status"], details=value)
        self.rows = [r for r in self.rows if r["id"] != row["id"]] + [row]


class Services:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.preflight_status = "READY"
        self.ready = True
        self.reconciled = True
        self.configured = True
        self.blockers = []

    def call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError("secret database password should never be recorded")

    def settle(self, day):
        self.call("settle")
        return dict(status="SUCCESS")

    def admit(self, day):
        self.call("admit")
        return dict(status="SUCCESS", paper_orders_created=0)

    def preflight(self, day, now):
        self.call("preflight")
        return dict(
            status=self.preflight_status,
            reasons=[] if self.preflight_status == "READY" else ["data_not_ready"],
        )

    def reconcile(self, day):
        self.call("reconcile")
        return dict(ok=self.reconciled)

    def snapshot(self, day):
        self.call("snapshot")
        return dict(nav=10000000)

    def regime(self, day):
        self.call("regime")
        return dict(
            data_ready=self.ready,
            capacity=4 if self.ready else 0,
            band="NEUTRAL" if self.ready else "UNAVAILABLE",
        )

    def allocation(self, day):
        self.call("allocation")
        return dict(
            blockers=self.blockers,
            upstream=dict(configuration_ready=self.configured),
            selected=0,
        )


def workflow():
    journal, services = Journal(), Services()
    runner = DailyOrchestrator(
        None, journal=journal, services=services, clock=lambda: NOW
    )
    return runner, journal, services


def test_full_sequence_and_success_is_replayed_without_writes():
    runner, journal, services = workflow()
    first = runner.run()
    assert first["status"] == "SUCCESS"
    assert services.calls == [
        "settle",
        "preflight",
        "reconcile",
        "snapshot",
        "regime",
        "allocation",
        "admit",
    ]
    writes = len(journal.writes)
    services.calls.clear()
    replay = runner.run()
    assert replay["replayed"] and replay["run_id"] == first["run_id"]
    assert services.calls == ["admit"] and len(journal.writes) == writes + 1
    assert replay["broker_orders_submitted"] == 0 and not replay["proposal_only"]


def test_busy_does_not_run_or_write():
    runner, journal, services = workflow()
    journal.acquired = False
    assert runner.run()["status"] == "BUSY"
    assert not services.calls and not journal.writes


@pytest.mark.parametrize("status", ["BLOCKED", "SKIPPED"])
def test_preflight_stops_downstream(status):
    runner, journal, services = workflow()
    services.preflight_status = status
    report = runner.run()
    assert report["status"] == status and services.calls == ["settle", "preflight"]
    assert journal.rows[-1]["status"] == status


def test_read_only_check_never_writes_or_locks():
    runner, journal, services = workflow()
    journal.acquired = False
    assert runner.run(DAY, check_only=True)["check_only"]
    assert services.calls == ["preflight"] and not journal.writes


def test_reconciliation_mismatch_stops_before_snapshot():
    runner, journal, services = workflow()
    services.reconciled = False
    report = runner.run()
    assert report["status"] == "BLOCKED"
    assert services.calls == ["settle", "preflight", "reconcile"]


@pytest.mark.parametrize(
    "failed", ["settle", "preflight", "reconcile", "snapshot", "regime", "allocation"]
)
def test_failed_stage_journal_and_safe_retry_from_start(failed):
    runner, journal, services = workflow()
    services.fail = failed
    report = runner.run()
    assert report["status"] == "FAILED"
    assert "secret" not in str(report)
    assert any(x["status"] == "FAILED" for x in report["stages"].values())
    services.fail = None
    services.calls.clear()
    retry = runner.run()
    assert retry["status"] == "SUCCESS" and retry["attempt"] == 2
    assert retry["previous_run_id"] == report["run_id"]
    assert services.calls == [
        "settle",
        "preflight",
        "reconcile",
        "snapshot",
        "regime",
        "allocation",
        "admit",
    ]
    assert len(journal.rows) == 2


def test_blocked_data_is_not_success_and_is_automatically_retried():
    runner, _, services = workflow()
    services.ready = False
    report = runner.run()
    assert report["status"] == "BLOCKED" and report["capacity"] == 0
    services.ready = True
    assert runner.run()["status"] == "SUCCESS"


def test_c_blocker_and_missing_configuration_are_reported():
    runner, _, services = workflow()
    services.configured = False
    services.blockers = ["no_account"]
    report = runner.run()
    assert set(report["reasons"]) == {"no_account", "module_a_configuration_not_ready"}


def test_defensive_zero_capacity_is_a_valid_success():
    runner, _, services = workflow()
    services.regime = lambda day: dict(data_ready=True, capacity=0, band="DEFENSIVE")
    assert runner.run()["status"] == "SUCCESS"


def test_explicit_rerun_refreshes_success_and_configuration_change_creates_new_key():
    runner, _, services = workflow()
    first = runner.run()
    assert runner.run(rerun=True)["attempt"] == 2
    runner.cfg = deepcopy(runner.cfg)
    runner.cfg["regime"]["healthy_capacity"] = 5
    changed = runner.run()
    assert changed["run_key"] != first["run_key"] and changed["attempt"] == 1


def test_abandoned_running_attempt_is_marked_interrupted():
    runner, journal, _ = workflow()
    result = runner.run()
    journal.rows[-1]["status"] = "RUNNING"
    journal.rows[-1]["details"]["status"] = "RUNNING"
    retry = runner.run()
    assert retry["status"] == "SUCCESS"
    assert (
        next(r for r in journal.rows if r["id"] == result["run_id"])["status"]
        == "INTERRUPTED"
    )


@pytest.mark.parametrize("day", [date(2026, 10, 15), date(2026, 10, 17)])
def test_persisted_historical_or_future_date_rejected(day):
    runner, journal, services = workflow()
    with pytest.raises(ValueError):
        runner.run(day)
    assert not journal.writes and not services.calls


def test_timezone_uses_ist_date_and_naive_clock_rejected():
    runner, _, _ = workflow()
    runner.clock = lambda: datetime.fromisoformat("2026-10-16T14:00:00+00:00")
    assert runner.run()["trade_date"] == "2026-10-16"
    runner.clock = lambda: datetime(2026, 10, 16)
    with pytest.raises(ValueError, match="timezone aware"):
        runner.run()


class Result:
    def __init__(self, rows=None, scalar=0):
        self.rows, self.value = rows, scalar

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def first(self):
        return self.rows


class Connection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, query, params=None):
        sql = str(query)
        self.engine.queries.append(sql)
        if "benchmark_code,count" in sql:
            return Result(rows=list(self.engine.counts.items()))
        if "daily_prices" in sql:
            return Result(scalar=self.engine.prices)
        if "data_quality" in sql:
            return Result(scalar=self.engine.errors)
        if "safety_state" in sql:
            return Result(rows=self.engine.safety)
        raise AssertionError(sql)


class Engine:
    def __init__(self):
        self.queries = []
        self.counts = dict.fromkeys(
            ["NIFTY50", "NIFTY500", "NIFTYMIDSMALLCAP400", "INDIAVIX"], 1
        )
        self.prices, self.errors = 100, 0
        self.safety = dict(emergency_stop=False, locked_reason=None)

    def connect(self):
        return Connection(self)


def preflight(monkeypatch, calendar=None, day=DAY, now=NOW, change=None):
    engine = Engine()
    if change:
        change(engine)
    monkeypatch.setattr(
        MarketCalendar,
        "from_database",
        lambda *a: calendar or MarketCalendar([DAY], {2026: set()}),
    )
    return DailyServices(engine, load_strategy_config()).preflight(day, now), engine


def test_preflight_requires_exact_session_not_five_day_stale_benchmark(monkeypatch):
    report, _ = preflight(monkeypatch, change=lambda e: e.counts.pop("INDIAVIX"))
    assert report["status"] == "BLOCKED"
    assert "INDIAVIX_session_missing_or_duplicate" in report["reasons"]


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda e: setattr(e, "prices", 0), "equity_prices_not_ready"),
        (lambda e: setattr(e, "errors", 1), "unresolved_data_quality_errors"),
        (
            lambda e: setattr(e, "safety", None),
            "execution_safety_locked_or_unavailable",
        ),
        (
            lambda e: setattr(
                e, "safety", dict(emergency_stop=True, locked_reason=None)
            ),
            "execution_safety_locked_or_unavailable",
        ),
    ],
)
def test_preflight_blocks_data_and_safety_failures(monkeypatch, change, reason):
    report, _ = preflight(monkeypatch, change=change)
    assert report["status"] == "BLOCKED" and reason in report["reasons"]


def test_weekend_skips_but_special_weekend_session_runs(monkeypatch):
    sunday = date(2026, 10, 18)
    report, engine = preflight(monkeypatch, day=sunday)
    assert report["status"] == "SKIPPED" and not engine.queries
    report, _ = preflight(
        monkeypatch,
        day=sunday,
        calendar=MarketCalendar([DAY, sunday], {2026: set()}, [sunday]),
    )
    assert report["status"] == "READY"


def test_unknown_calendar_and_pre_close_skip(monkeypatch):
    report, engine = preflight(monkeypatch, calendar=MarketCalendar())
    assert report["status"] == "BLOCKED" and not engine.queries
    report, engine = preflight(monkeypatch, now=NOW.replace(hour=15, minute=29))
    assert report["status"] == "SKIPPED" and not engine.queries
    assert (
        preflight(monkeypatch, now=NOW.replace(hour=15, minute=30))[0]["status"]
        == "READY"
    )


def test_transaction_pooler_rejected():
    with pytest.raises(ValueError, match="session pooler"):
        RunJournal(SimpleNamespace(url="postgresql://postgres@host:6543/postgres"))


class LockConnection:
    invalidated = False

    def __init__(self, *, acquired=True):
        self.acquired = acquired
        self.queries = []
        self.pid = 123
        self.fail_pid = False
        self.fail_unlock = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        if "pg_try_advisory_lock" in sql:
            return Result(scalar=self.acquired)
        if "pg_advisory_unlock" in sql:
            if self.fail_unlock:
                raise RuntimeError("unlock failed")
            return Result(scalar=True)
        if "pg_backend_pid" in sql:
            if self.fail_pid:
                self.fail_pid = False
                raise RuntimeError("pid failed")
            return Result(scalar=self.pid)
        raise AssertionError(sql)

    def commit(self):
        pass

    def rollback(self):
        pass

    def invalidate(self):
        self.invalidated = True


def lock_journal(connection):
    return RunJournal(
        SimpleNamespace(
            url="postgresql://postgres@host:5432/postgres", connect=lambda: connection
        )
    )


@pytest.mark.parametrize("raise_body", [False, True])
def test_session_lock_released_on_success_and_body_failure(raise_body):
    connection = LockConnection()
    journal = lock_journal(connection)
    try:
        with journal.exclusive() as acquired:
            assert acquired and journal._connection is connection
            if raise_body:
                raise ValueError("stage failure")
    except ValueError:
        assert raise_body
    assert any("pg_advisory_unlock" in sql for sql in connection.queries)
    assert journal._connection is None


def test_contended_session_never_unlocks_another_owner():
    connection = LockConnection(acquired=False)
    with lock_journal(connection).exclusive() as acquired:
        assert not acquired
    assert not any("pg_advisory_unlock" in sql for sql in connection.queries)


def test_lock_initialization_failure_still_releases_lock():
    connection = LockConnection()
    connection.fail_pid = True
    with pytest.raises(RuntimeError, match="pid failed"):
        with lock_journal(connection).exclusive():
            pytest.fail("body must not run")
    assert any("pg_advisory_unlock" in sql for sql in connection.queries)


def test_unlock_failure_invalidates_pool_connection():
    connection = LockConnection()
    connection.fail_unlock = True
    with pytest.raises(RuntimeError, match="unlock failed"):
        with lock_journal(connection).exclusive():
            pass
    assert connection.invalidated


def test_changed_backend_session_prevents_journal_writes():
    connection = LockConnection()
    journal = lock_journal(connection)
    with journal.exclusive():
        connection.pid = 999
        with pytest.raises(RuntimeError, match="session changed"):
            journal.save({})


def test_journal_requires_lock_owner():
    with pytest.raises(RuntimeError, match="lock connection unavailable"):
        lock_journal(LockConnection()).save({})


def test_cli_exit_codes_make_blocked_and_failed_runs_visible(monkeypatch, capsys):
    from scripts import run_market_close

    for status, code in [
        ("SUCCESS", 0),
        ("SKIPPED", 0),
        ("BLOCKED", 2),
        ("BUSY", 3),
        ("FAILED", 1),
    ]:
        monkeypatch.setattr(
            run_market_close, "run", lambda *a, **kw: dict(status=status)
        )
        assert run_market_close.main([]) == code
        assert status in capsys.readouterr().out


def test_protective_settlement_runs_before_blocked_preflight():
    runner, journal, services = workflow()
    services.preflight_status = "BLOCKED"
    report = runner.run()
    assert services.calls == ["settle", "preflight"] and report["status"] == "BLOCKED"


def test_failed_settlement_blocks_new_decisions():
    runner, journal, services = workflow()
    services.settle = lambda day: dict(status="BLOCKED")
    assert runner.run()["reasons"] == ["paper_settlement_requires_review"]
    assert services.calls == ["preflight"]


def test_admission_failure_is_durable_and_safe_to_retry():
    runner, journal, services = workflow()
    services.fail = "admit"
    first = runner.run()
    assert first["status"] == "FAILED" and first["reasons"] == [
        "paper_admission_failed"
    ]
    services.fail = None
    assert runner.run()["status"] == "SUCCESS"
