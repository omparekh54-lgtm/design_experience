from copy import deepcopy
from datetime import date, timedelta
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.main_exec import app
from apps.api import matrix_routes
from packages.pms.backtest_matrix import Cell, Period, core_cases
from packages.pms.config import load_strategy_config
from packages.pms.matrix_data import (
    INPUTS,
    MatrixGates,
    ReadOnlySnapshot,
    input_manifest,
    persist_report,
    consistent_inputs,
)
from scripts.run_backtest_matrix import main

CFG = load_strategy_config()
DAY = date(2020, 1, 2)
ID = UUID("00000000-0000-4000-8000-000000000001")
CELL = Cell(Period("TEST", DAY, DAY + timedelta(days=3)), core_cases()[0])


class Result:
    rowcount = 1

    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.value

    def first(self):
        return self.value

    def scalar_one(self):
        return self.value


class Engine:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.closed = 0

    def connect(self):
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed += 1

    def execution_options(self, **kw):
        self.options = kw
        return self

    def execute(self, query, params=None):
        self.calls.append((str(query), params or {}))
        if not self.values:
            pytest.fail("Unexpected SQL query")
        return Result(self.values.pop(0))


def test_snapshot_is_repeatable_read_database_enforced_read_only_and_nested_scope_survives():
    engine = Engine([None, None, None, 42])
    with consistent_inputs(engine) as snapshot:
        with snapshot.connect() as nested:
            assert nested.execute(text("select 42")).scalar_one() == 42
        assert engine.closed == 0
        with pytest.raises(RuntimeError):
            snapshot.begin()
    assert engine.options == {"isolation_level": "REPEATABLE READ"}
    assert [q for q, _ in engine.calls[:3]] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL search_path TO pms,public",
        "SET LOCAL TIME ZONE 'UTC'",
    ]


def manifest_values():
    return [[{"bucket": "aa", "rows": 1, "digest": "test"}] for _ in INPUTS] + [
        [DAY],
        [
            dict(
                id=1,
                instrument_id="NSE:TEST",
                listed_from=date(2010, 1, 1),
                delisted_on=None,
            )
        ],
    ]


def test_manifest_hashes_content_and_includes_inactive_historical_master():
    engine = Engine(manifest_values())
    first = input_manifest(engine, [CELL])
    assert first["scope"] == "HISTORICAL_MASTER"
    assert len(first["fingerprint"]) == 64
    assert "active" not in engine.calls[-1][0]
    assert all("pms." in sql for sql, _ in engine.calls)
    altered = manifest_values()
    altered[0][0]["digest"] = "changed"
    assert (
        input_manifest(Engine(altered), [CELL])["fingerprint"] != first["fingerprint"]
    )
    assert (
        input_manifest(Engine(manifest_values()), [CELL], ["NSE:TEST"])["scope"]
        == "EXPLICIT_SYMBOL_SAMPLE"
    )


def test_manifest_rejects_unknown_requested_symbol():
    with pytest.raises(ValueError, match="Unknown"):
        input_manifest(Engine(manifest_values()), [CELL], ["NSE:MISSING"])


def manifest():
    return dict(
        universe=[dict(id=1, listed_from="2010-01-01")],
        tables={
            name: dict(rows=0 if name == "data_quality_issues" else 1)
            for name in INPUTS
        },
    )


def test_incomplete_prices_block_and_coverage_is_reused_across_variants():
    engine = Engine(
        [
            [dict(id=1, expected=260, observed=20, invalid=0, unverified_raw_source=0)],
            [DAY, DAY + timedelta(days=1)],
            257,
        ]
    )
    gate = MatrixGates(engine, manifest())
    reasons = gate(CELL, CFG)
    assert (
        "historical_price_sessions_missing_invalid_or_adjustment_unverified" in reasons
    )
    assert len(gate.samples["TEST"]) == 1
    assert gate(CELL, CFG) == reasons and len(engine.calls) == 3
    assert engine.calls[0][1]["ids"] == "[1]" and "adjusted" in engine.calls[0][1]


def test_missing_policy_and_legacy_surveillance_are_visible_not_disabled():
    engine = Engine([[], [DAY, DAY + timedelta(days=1)], 257])
    gate = MatrixGates(engine, manifest())
    cfg = deepcopy(CFG)
    cfg["fundamental"]["leverage_limits"]["default"]["max_debt_to_equity"] = None
    reasons = gate(Cell(CELL.period, core_cases()[2]), cfg)
    assert "module_a_default_leverage_policy_unconfigured" in reasons
    assert "historical_surveillance_not_verified" in reasons
    assert CFG["fundamental"]["legacy_surveillance_verified"] is False


def test_truncated_calendar_and_unknown_listing_never_pass():
    m = manifest()
    m["universe"][0]["listed_from"] = None
    gate = MatrixGates(Engine([[], [], 0]), m)
    reasons = gate(CELL, CFG)
    assert "historical_listing_dates_unavailable" in reasons
    assert "official_calendar_window_missing_or_truncated" in reasons
    assert "official_warmup_calendar_fewer_than_257_sessions" in reasons


def test_persistence_writes_only_research_and_bounds_daily_diagnostics():
    report = dict(
        run_id=str(ID),
        identity="fixture",
        dataset=dict(start="2020-01-01", end="2020-01-02"),
        config=CFG,
        code_version="fixture",
        scope="RESEARCH_ONLY",
        holdout="reserved",
        summary={},
        cells=[
            dict(
                diagnostics=dict(
                    regime=[1] * 10000,
                    allocation=[1] * 10000,
                    regime_summary={"0": 10000},
                )
            )
        ],
    )
    engine = Engine([None])
    persist_report(engine, report)
    sql, params = engine.calls[0]
    assert "insert into pms.research_runs" in sql and "BACKTEST_MATRIX" in sql
    payload = json.loads(params["metrics"])
    assert "regime" not in payload["cells"][0]["diagnostics"]
    assert payload["cells"][0]["diagnostics"]["regime_summary"] == {"0": 10000}
    assert len(report["cells"][0]["diagnostics"]["regime"]) == 10000


def test_matrix_read_api_cannot_read_another_research_type(monkeypatch):
    engine = Engine([[]])
    monkeypatch.setattr(matrix_routes, "_engine", lambda: engine)
    response = TestClient(app).get("/v1/research/matrix")
    assert response.status_code == 200 and response.json() == {"count": 0, "runs": []}
    assert "run_type='BACKTEST_MATRIX'" in engine.calls[0][0]
    assert TestClient(app).post("/v1/research/matrix", json={}).status_code == 405


def test_matrix_detail_pagination_filters_and_read_only_boundary(monkeypatch):
    engine = Engine(
        [dict(id=ID, summary={}), 250, [dict(key="fixture", status="BLOCKED")]]
    )
    monkeypatch.setattr(matrix_routes, "_engine", lambda: engine)
    response = TestClient(app).get(
        f"/v1/research/matrix/{ID}?status=BLOCKED&family=CORE&page=2"
    )
    assert response.status_code == 200 and response.json()["total"] == 250
    assert response.json()["acceptance"] == "NOT_CERTIFIED"
    assert engine.calls[-1][1]["offset"] == 50
    assert engine.calls[-1][1]["family"] == "CORE"
    assert all(q.lstrip().lower().startswith("select") for q, _ in engine.calls)
    assert all("run_type='BACKTEST_MATRIX'" in q for q, _ in engine.calls)


@pytest.mark.parametrize(
    "suffix", ["?page=0", "?status=PASS", "?family=INVALID", "?limit=1000"]
)
def test_invalid_matrix_filters_rejected(suffix):
    assert TestClient(app).get(f"/v1/research/matrix/{ID}" + suffix).status_code == 422


def test_matrix_not_found_and_invalid_uuid(monkeypatch):
    monkeypatch.setattr(matrix_routes, "_engine", lambda: Engine([None]))
    assert TestClient(app).get(f"/v1/research/matrix/{ID}").status_code == 404
    assert TestClient(app).get("/v1/research/matrix/invalid").status_code == 422


def test_cli_plan_is_offline_and_has_all_128_cells(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PMS_DATABASE_URL", raising=False)
    assert main(["--plan"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cells"] == 128 and data["complete_design"]
    assert max(row["period"]["end"] for row in data["plan"]) == "2024-12-31"


def test_cli_invalid_capital_and_partial_period_require_no_database():
    with pytest.raises(SystemExit):
        main(["--plan", "--capital", "nan"])
    with pytest.raises(SystemExit):
        main(["--plan", "--start", "2020-01-01"])


def test_report_lock_excludes_concurrent_writers_and_releases_after_exit(tmp_path):
    from packages.pms.matrix_data import report_lock

    path = tmp_path / "report.json"
    with report_lock(path):
        with pytest.raises(RuntimeError, match="Another matrix"):
            with report_lock(path):
                pass
    with report_lock(path):
        pass


def test_coverage_cache_does_not_hide_a_stricter_adjusted_source_policy():
    good = dict(id=1, expected=260, observed=260, invalid=0, unverified_raw_source=0)
    bad = {**good, 'unverified_raw_source': 1}
    engine = Engine([[good], [DAY, DAY+timedelta(days=1)], 257,
                     [bad], [DAY, DAY+timedelta(days=1)], 257])
    gate = MatrixGates(engine, manifest())
    cfg = deepcopy(CFG)
    assert not gate(CELL, cfg)
    cfg['technical']['adjusted_price_sources'] = ['NSE_UDIFF']
    assert 'historical_price_sessions_missing_invalid_or_adjustment_unverified' in gate(CELL, cfg)
    assert len(engine.calls) == 6
