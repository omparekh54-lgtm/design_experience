from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import pytest

from packages.pms.formal_paper import FormalPaperExecution, Budget, VERSION, fingerprint
from packages.pms.module_a_context import IST
from packages.pms.config import load_strategy_config
from packages.pms.trade_engine import TradePosition, activate_entry
from packages.pms.market_calendar import MarketCalendar

D = date(2026, 10, 19)
CFG = load_strategy_config()


class Result:
    def __init__(self, value=None):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class Store:
    def __init__(self):
        self.statements = []
        self.ids = set()
        self.proposals = []
        self.run = dict(
            status="SUCCESS",
            details=dict(
                run_id="run-1",
                config_hash=fingerprint(CFG),
                mode="PAPER",
                implementation="DAILY_ORCHESTRATOR_v1.1",
                started_at="2026-10-19T18:59:00+05:30",
                finished_at="2026-10-19T19:00:00+05:30",
                stages={
                    "paper_settlement": {"result": {"status": "SUCCESS"}},
                    "paper_reconciliation": {"result": {"ok": True}},
                },
            ),
        )
        self.safety = dict(
            execution_mode="PAPER",
            emergency_stop=False,
            locked_reason=None,
            new_orders_enabled=True,
        )
        self.regime = dict(
            strategy_version=CFG["version"],
            max_open_trades=4,
            hard_override=False,
            inputs={
                "_module_e_version": CFG["regime"]["implementation_version"],
                "_data_quality": {"data_ready": True},
            },
        )
        self.errors = 0

    @contextmanager
    def begin(self):
        yield self

    @contextmanager
    def begin_nested(self):
        yield self

    def execute(self, q, p=None):
        sql = str(q)
        self.statements.append((sql, deepcopy(p)))
        if sql.startswith("select status,details"):
            return Result(self.run)
        if sql.startswith("select s.decision"):
            return Result(
                [
                    dict(
                        decision=p,
                        source_instrument_pk=p["instrument_pk"],
                        source_symbol=p["instrument_id"],
                    )
                    for p in self.proposals
                ]
            )
        if sql.startswith("select id from trades"):
            return Result(p["id"] if p["id"] in self.ids else None)
        if sql.startswith("insert into trades"):
            self.ids.add(p["id"])
        if "from broker_fills f" in sql:
            return Result(0)
        if "from safety_state" in sql:
            return Result(self.safety)
        if "from data_quality_issues" in sql:
            return Result(self.errors)
        if "from market_regime_history" in sql:
            return Result(self.regime)
        return Result(None)


def setup(monkeypatch):
    store = Store()
    service = FormalPaperExecution(
        store, clock=lambda: datetime(2026, 10, 19, 19, tzinfo=IST)
    )
    calendar = MarketCalendar([date(2026, 10, 16), D], {2026: set()})
    monkeypatch.setattr(
        service, "session", lambda day=None: (D, calendar, "READY", None)
    )
    monkeypatch.setattr(service, "_rows", lambda *a, **kw: [])
    monkeypatch.setattr(
        service, "_account", lambda *a: (Budget(1000000, 1000000, 0, 0), [])
    )
    monkeypatch.setattr(service, "_persist", lambda *a: None)
    signal = dict(
        setup="SETUP_1",
        status="ACTIVE",
        signal_date="2026-10-16",
        signal_high=100,
        signal_low=96,
    )
    store.proposals = [
        dict(
            instrument_id="NSE:TEST",
            instrument_pk=1,
            sector="IT",
            signal=signal,
            buy_stop=100,
            shares=1000,
            qualified=True,
            score=90,
            stats={"adtv": 10000000},
        )
    ]
    monkeypatch.setattr(
        service.b,
        "evaluate",
        lambda *a, **kw: SimpleNamespace(
            eligible_universe=True, research_only=False, signal=signal
        ),
    )
    return service, store, calendar


def inserts(store, name):
    return [(q, p) for q, p in store.statements if q.startswith("insert into " + name)]


def test_admit_writes_only_paper_and_replay_creates_no_duplicate(monkeypatch):
    service, store, _ = setup(monkeypatch)
    first = service.admit()
    assert first["paper_orders_created"] == 1
    assert (
        len(inserts(store, "trades")) == 1 and len(inserts(store, "broker_orders")) == 1
    )
    assert "'PAPER','PAPER'" in inserts(store, "broker_orders")[0][0]
    assert not inserts(store, "broker_fills") and not inserts(store, "portfolio_ledger")
    replay = service.admit()
    assert replay["paper_orders_created"] == 0 and replay["orders"][0]["replayed"]
    assert len(inserts(store, "trades")) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: setattr(s, "run", None),
        lambda s: s.run.update(status="BLOCKED"),
        lambda s: s.run["details"].update(config_hash="wrong"),
        lambda s: s.run["details"].update(mode="LIVE"),
        lambda s: s.safety.update(new_orders_enabled=False),
        lambda s: s.safety.update(execution_mode="LIVE_APPROVAL"),
        lambda s: s.safety.update(emergency_stop=True),
        lambda s: s.safety.update(locked_reason="data_gap"),
        lambda s: setattr(s, "errors", 1),
        lambda s: s.regime.update(strategy_version="old"),
        lambda s: s.regime["inputs"]["_data_quality"].update(data_ready=False),
        lambda s: s.regime.update(max_open_trades=0),
    ],
)
def test_admission_gates_do_not_create_orders(monkeypatch, mutate):
    service, store, _ = setup(monkeypatch)
    mutate(store)
    report = service.admit()
    assert report["status"] == "BLOCKED"
    assert not inserts(store, "trades") and not inserts(store, "broker_orders")


def test_gap_budget_limits_are_rechecked_at_admission(monkeypatch):
    service, store, _ = setup(monkeypatch)
    monkeypatch.setattr(
        service, "_account", lambda *a: (Budget(1000000, 1000000, 35000, 0), [])
    )
    report = service.admit()
    assert report["paper_orders_created"] == 0 and report["rejected"]
    assert not inserts(store, "trades")


def test_changed_native_b_signal_cannot_reuse_allocated_proposal(monkeypatch):
    service, store, _ = setup(monkeypatch)
    monkeypatch.setattr(
        service.b,
        "evaluate",
        lambda *a, **kw: SimpleNamespace(
            eligible_universe=True,
            research_only=False,
            signal={**store.proposals[0]["signal"], "signal_high": 101},
        ),
    )
    assert service.admit()["paper_orders_created"] == 0


def owned_pending():
    pos = TradePosition(
        "NSE:TEST",
        date(2026, 10, 16),
        100,
        96,
        1000000,
        entry_sessions=(D, D + timedelta(days=1), D + timedelta(days=2)),
    )
    activate_entry(pos)
    return dict(
        id="trade-1",
        instrument_id=1,
        signal_date=pos.signal_date,
        symbol="NSE:TEST",
        state="PENDING_ENTRY",
        remaining_shares=0,
        shares=1000,
        entry=100,
        stop=95.52,
        order_status="OPEN",
        entry_order_id="order-1",
        order_created_at=datetime(2026, 10, 16, 19, tzinfo=IST),
        metadata=dict(
            implementation=VERSION,
            sector="IT",
            score=90,
            admitted_on="2026-10-16",
            admitted_shares=1000,
            config_hash=fingerprint(CFG),
            checkpoint=pos.checkpoint(),
        ),
    )


def test_settlement_replay_does_not_write_second_fill(monkeypatch):
    service, store, _ = setup(monkeypatch)
    row = owned_pending()
    writes = []
    monkeypatch.setattr(service, "_rows", lambda *a, **kw: [row])

    def account(*a):
        budget = Budget(1000000, 1000000, 0, 0)
        if row["state"] == "PENDING_ENTRY":
            budget.reserve("NSE:TEST", "IT", 100, 95.52, 1000, 268.74)
        return budget, []

    monkeypatch.setattr(service, "_account", account)
    monkeypatch.setattr(
        service,
        "_inputs",
        lambda *a: (
            dict(open=100, high=110, low=99, close=108),
            dict(ema9=102, previous_ema9=101, previous_ema21=100),
            10000000,
            True,
            True,
        ),
    )

    def persist(conn, r, meta, fills, day, old):
        writes.extend(fills)
        r["metadata"] = meta
        r["state"] = meta["checkpoint"]["state"]
        r["remaining_shares"] = meta["checkpoint"]["remaining_shares"]

    monkeypatch.setattr(service, "_persist", persist)
    assert service.settle()["status"] == "SUCCESS"
    assert len(writes) == 1
    assert service.settle()["status"] == "SUCCESS" and len(writes) == 1


def test_newly_admitted_close_order_never_uses_same_day_bar(monkeypatch):
    service, store, _ = setup(monkeypatch)
    row = owned_pending()
    row["metadata"]["admitted_on"] = str(D)
    monkeypatch.setattr(service, "_rows", lambda *a, **kw: [row])
    monkeypatch.setattr(
        service,
        "_inputs",
        lambda *a: pytest.fail("No same-day bar for newly admitted order"),
    )
    service.settle()


def test_disabled_entry_does_not_consult_missing_bar_to_cancel(monkeypatch):
    service, store, _ = setup(monkeypatch)
    row = owned_pending()
    captured = []
    store.safety["new_orders_enabled"] = False
    monkeypatch.setattr(service, "_rows", lambda *a, **kw: [row])
    monkeypatch.setattr(
        service, "_persist", lambda conn, row, meta, *a: captured.append(meta)
    )
    monkeypatch.setattr(
        service,
        "_inputs",
        lambda *a: pytest.fail("Gate cancellation must not require prices"),
    )
    service.settle()
    assert captured[0]["checkpoint"]["state"] == "CANCELLED"


def test_prior_day_regime_is_queried_at_entry_not_current_close(monkeypatch):
    service, store, calendar = setup(monkeypatch)
    service._entry_gate(store, D, calendar)
    query, params = next(
        (q, p) for q, p in store.statements if "from market_regime_history" in q
    )
    assert params["d"] == date(2026, 10, 16)
