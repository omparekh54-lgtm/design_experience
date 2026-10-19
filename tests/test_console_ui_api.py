from datetime import date, datetime
from types import SimpleNamespace
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from apps.api.main_exec import app
from apps.api import console_routes as console
from packages.pms.config import load_strategy_config

CLIENT = TestClient(app)
DAY = date(2026, 10, 16)
CFG = load_strategy_config()


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def first(self):
        return self.value

    def one(self):
        return self.value

    def all(self):
        return self.value


class Engine:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        query = str(sql)
        assert query.lstrip().lower().startswith("select"), query
        self.calls.append((query, params or {}))
        assert self.responses, "Unexpected database query"
        return Result(self.responses.pop(0))


def attach(monkeypatch, values):
    engine = Engine(values)
    monkeypatch.setattr(console, "_engine", lambda: engine)
    return engine


def test_overview_distinguishes_no_account_from_zero_and_is_read_only(monkeypatch):
    engine = attach(
        monkeypatch,
        [
            DAY,
            None,
            [],
            None,
            None,
            None,
            None,
            None,
            dict(total=0, selected=0, qualified=0),
        ],
    )
    response = CLIENT.get("/v1/console/overview?as_of=2026-10-16")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio"] is None and data["safety"] is None
    assert data["market_session"] == "2026-10-16" and data["regime"] is None
    assert data["nav_history"] == [] and data["real_money"] is False
    assert all("pms." in q for q, _ in engine.calls)


def test_unverified_regime_never_displays_positive_capacity():
    row = dict(
        trade_date=DAY,
        strategy_version=CFG["version"],
        band="STRONG",
        max_open_trades=7,
        inputs={"_module_e_version": "old", "_data_quality": {"data_ready": True}},
    )
    result = console.normalize_regime(row, DAY, DAY)
    assert (
        result["data_ready"] is False
        and result["max_open_trades"] == 0
        and result["band"] == "UNAVAILABLE"
    )
    row["inputs"]["_module_e_version"] = CFG["regime"]["implementation_version"]
    assert console.normalize_regime(row, DAY, DAY)["data_ready"]
    assert not console.normalize_regime(row, DAY, date(2026, 10, 26))["data_ready"]


def test_signals_filters_and_pagination_are_bound_not_interpolated(monkeypatch):
    engine = attach(
        monkeypatch, [DAY, dict(total=2302, selected=1, watchlist=2, rejected=2299), []]
    )
    response = CLIENT.get(
        "/v1/console/signals",
        params=dict(as_of="2026-10-16", view="rejected", q="ABC' OR 1=1", page=2),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2299 and data["counts"]["all"] == 2302
    assert data["decision_date"] == "2026-10-16" and data["proposal_only"]
    sql, params = engine.calls[-1]
    assert (
        "ABC'" not in sql
        and params["offset"] == 50
        and params["search"] == "%ABC' OR 1=1%"
    )


@pytest.mark.parametrize("suffix", ["?view=unsafe", "?page=0", "?limit=1000"])
def test_invalid_signal_filters_rejected(suffix):
    assert CLIENT.get("/v1/console/signals" + suffix).status_code == 422


def test_future_market_view_rejected_before_database(monkeypatch):
    monkeypatch.setattr(
        console, "_engine", lambda: pytest.fail("No DB call for invalid future date")
    )
    assert CLIENT.get("/v1/console/overview?as_of=2099-01-01").status_code == 400


def test_formal_paper_queries_never_include_sandbox_and_do_not_invent_snapshot(
    monkeypatch,
):
    engine = attach(monkeypatch, [DAY, None, [], [], []])
    response = CLIENT.get("/v1/console/paper")
    assert response.status_code == 200 and response.json()["snapshot"] is None
    assert response.json()["mode"] == "PAPER"
    assert "o.mode='PAPER'" in engine.calls[-1][0]
    assert "o.mode<>'PAPER'" in engine.calls[2][0]


def test_trade_detail_checks_mode_and_not_found(monkeypatch):
    engine = attach(monkeypatch, [None])
    assert (
        CLIENT.get(
            "/v1/console/trades/00000000-0000-0000-0000-000000000001"
        ).status_code
        == 404
    )
    assert "o.mode='PAPER'" in engine.calls[0][0]
    assert CLIENT.get("/v1/console/trades/not-a-uuid").status_code == 422


def test_data_status_does_not_turn_zero_dq_issues_into_complete_coverage(monkeypatch):
    attach(
        monkeypatch,
        [
            DAY,
            dict(
                active_stocks=2302,
                classified_stocks=2302,
                stocks_with_session_bar=100,
                financial_filings=0,
                critical_errors=0,
            ),
            [],
            [],
            None,
        ],
    )
    response = CLIENT.get("/v1/console/data")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["stocks_with_session_bar"] == 100
    assert (
        data["regime_data_quality"] is None
        and "does not prove" in data["coverage_note"]
    )


def test_readiness_get_never_persists_a_record(monkeypatch):
    from apps.api import execution_routes

    class Controller:
        def __init__(self, *args):
            pass

        def evaluate(self):
            return SimpleNamespace(stage="PAPER", gates={})

        def persist(self, *args, **kw):
            pytest.fail("GET must remain read only")

    monkeypatch.setattr(execution_routes, "_engine", lambda: None)
    monkeypatch.setattr(execution_routes, "ProductionReadinessController", Controller)
    assert CLIENT.get("/v1/execution/readiness").status_code == 200


def test_sandbox_mutation_requires_operator_header_even_from_dashboard(monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "private-test-only")
    response = CLIENT.post(
        "/v1/execution/paper-sandbox/orders",
        json=dict(instrument_id="NSE:ABC", side="BUY", quantity=1),
    )
    assert response.status_code == 401


def test_sandbox_accepts_idempotency_token_without_crossing_account_mode(monkeypatch):
    from apps.api import execution_routes

    recorded = []

    class Broker:
        def place_order(self, request):
            recorded.append(request)
            return SimpleNamespace(order_id="example", status="OPEN")

    monkeypatch.setenv("OPS_API_KEY", "private-test-only")
    monkeypatch.setattr(execution_routes, "_sandbox_broker", lambda: Broker())
    response = CLIENT.post(
        "/v1/execution/paper-sandbox/orders",
        headers={"x-ops-key": "private-test-only"},
        json=dict(
            instrument_id="NSE:ABC",
            side="BUY",
            quantity=1,
            idempotency_key="ui-test-request",
        ),
    )
    assert (
        response.status_code == 200
        and response.json()["account_mode"] == "PAPER_SANDBOX"
    )
    assert recorded[0].idempotency_key == "ui-test-request"
