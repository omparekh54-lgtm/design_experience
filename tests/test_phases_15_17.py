from datetime import date

from fastapi.testclient import TestClient

from apps.api.main import app
from packages.pms.stress_testing import StressPeriod, StressResult, summarize_stress_results
from packages.pms.walk_forward import WalkForwardResult, WalkForwardWindow, SelectedParameters, summarize_walk_forward


def test_stress_summary_reports_worst_periods():
    period1 = StressPeriod("A", date(2020, 1, 1), date(2020, 3, 1))
    period2 = StressPeriod("B", date(2022, 1, 1), date(2022, 12, 31))
    summary = summarize_stress_results([
        StressResult(period1, {"total_return_pct": -8.0, "max_drawdown_pct": 18.0}),
        StressResult(period2, {"total_return_pct": 5.0, "max_drawdown_pct": 12.0}),
    ])
    assert summary["periods"] == 2
    assert summary["positive_periods"] == 1
    assert summary["worst_drawdown_pct"] == 18.0
    assert summary["worst_return_pct"] == -8.0


def test_walk_forward_summary_uses_only_oos_metrics():
    w1 = WalkForwardWindow("W1", date(2018,1,1), date(2019,12,31), date(2020,1,1), date(2020,12,31))
    w2 = WalkForwardWindow("W2", date(2019,1,1), date(2020,12,31), date(2021,1,1), date(2021,12,31))
    p = SelectedParameters(3.0, 15.0, 0.5, 1.5, 10.0)
    rows = [
        WalkForwardResult(w1, p, {"total_return_pct": 100}, {"total_return_pct": -2, "max_drawdown_pct": 15}),
        WalkForwardResult(w2, p, {"total_return_pct": -100}, {"total_return_pct": 6, "max_drawdown_pct": 9}),
    ]
    summary = summarize_walk_forward(rows)
    assert summary["positive_oos_windows"] == 1
    assert summary["average_oos_return_pct"] == 2.0
    assert summary["worst_oos_drawdown_pct"] == 15


def test_api_health_does_not_require_database_connection(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["live_trading"] is False
    assert response.json()["database"] == "not_configured"


def test_openapi_contains_phase_17_routes():
    paths = app.openapi()["paths"]
    expected = {
        "/v1/universe",
        "/v1/module-a/{instrument_id}",
        "/v1/module-b/{instrument_id}",
        "/v1/module-c",
        "/v1/module-e/{trade_date}",
        "/v1/portfolio",
        "/v1/positions",
        "/v1/orders",
        "/v1/trades",
        "/v1/backtests",
        "/v1/research/runs",
        "/v1/research/stress",
        "/v1/research/walk-forward",
        "/v1/data-health",
    }
    assert expected.issubset(set(paths))
