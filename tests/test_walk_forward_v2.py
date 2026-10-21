from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.main_exec import app
from apps.api import walk_forward_routes
from packages.pms.backtest import EventDrivenBacktester, MarketBar, Signal
from packages.pms.backtest_matrix import fingerprint, plain
from packages.pms.config import load_strategy_config
from packages.pms.matrix_replay import ResearchDataGap
from packages.pms.walk_forward import (
    DEFAULT_WINDOWS,
    FINAL_WINDOW,
    WalkForwardWindow,
    WalkForwardRunner,
    SelectionPolicy,
    candidate_cases,
    validate_plan,
    phase_cells,
    select_candidate,
)
from packages.pms.walk_forward_data import (
    prepare_inputs,
    persist_walk_forward,
    replay_executor,
)
from scripts.run_walk_forward import main, export_csv
from test_matrix_data_api import Engine

CFG = load_strategy_config()
W = WalkForwardWindow(
    "TEST", date(2018, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 12, 31)
)
CASES = candidate_cases(CFG, quick=True)[:2]
DATA = {
    W.name: {
        p: dict(fingerprint=p, universe=[dict(instrument_id="NSE:TEST")])
        for p in ("TRAIN", "TEST")
    }
}


def replay(cell, cfg, return_scale=1):
    # Actual lifecycle and costs; one independent cash account per train/test call.
    start = cell.period.start
    from datetime import timedelta

    days = [start + timedelta(days=n) for n in range(5)]
    bars = [
        MarketBar("NSE:TEST", days[0], 100, 101, 99, 100, 100, 99, 98),
        MarketBar("NSE:TEST", days[1], 102, 103, 101, 102, 101, 100, 99),
        MarketBar("NSE:TEST", days[2], 103, 105, 102, 104, 102, 101, 100),
        MarketBar("NSE:TEST", days[3], 103, 104, 90, 95, 100, 102, 101),
        MarketBar("NSE:TEST", days[4], 95, 97, 94, 96, 99, 100, 102),
    ]
    r = EventDrivenBacktester(cfg=cfg).run(
        bars,
        lambda day: [Signal("NSE:TEST", days[0], 101, 97)] if day == days[0] else [],
    )
    # Alter only the marked NAV to make training/test ordering deliberately disagree.
    r.equity_curve[:] = [
        (d, v * return_scale if i else v) for i, (d, v) in enumerate(r.equity_curve)
    ]
    return r, dict(isolated=True)


def runner(tmp_path, executor=None, gate=None, **kw):
    return WalkForwardRunner(
        CFG,
        (W,),
        CASES,
        deepcopy(DATA),
        tmp_path / "report.json",
        executor=executor or (lambda w, p, c, cfg: replay(c, cfg)),
        gate=gate or (lambda *a: []),
        code_version="test-code",
        policy=SelectionPolicy(1),
        **kw,
    )


def test_design_is_fixed_grid_and_holdout_is_separate():
    cfg = deepcopy(CFG)
    cases = candidate_cases(cfg)
    assert len(cases) == 27 and len(candidate_cases(cfg, quick=True)) == 4
    assert len(DEFAULT_WINDOWS) == 3 and all(
        w.test_end < date(2025, 1, 1) for w in DEFAULT_WINDOWS
    )
    assert FINAL_WINDOW.kind == "FINAL_HOLDOUT"
    assert all(c.config(cfg)["risk"] == cfg["risk"] for c in cases)
    assert cfg == CFG
    assert max(c.period.end for c in phase_cells(FINAL_WINDOW, cases, "TEST")) == date(
        2025, 12, 31
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"train_end": date(2020, 1, 1)},
        {"test_end": date(2019, 1, 1)},
        {"test_end": date(2025, 1, 1)},
        {"kind": "UNKNOWN"},
        {"name": ""},
        {"train_start": date(2020, 1, 1)},
        {"test_end": date(2099, 1, 1)},
    ],
)
def test_invalid_boundaries_rejected(changes):
    with pytest.raises(ValueError):
        replace(W, **changes)


def test_final_boundary_must_be_explicit_and_training_never_consumes_holdout():
    with pytest.raises(ValueError):
        replace(FINAL_WINDOW, kind="RETROSPECTIVE")
    with pytest.raises(ValueError):
        replace(FINAL_WINDOW, train_end=date(2025, 1, 1), test_start=date(2025, 1, 2))
    with pytest.raises(ValueError):
        replace(W, kind="FINAL_HOLDOUT")


@pytest.mark.parametrize(
    "windows,cases",
    [
        ((), CASES),
        ((W, W), CASES),
        ((W,), ()),
        ((W, replace(W, name="OVERLAP")), CASES),
        ((W,), (CASES[0], CASES[0])),
    ],
)
def test_invalid_plan_rejected(windows, cases):
    with pytest.raises(ValueError):
        validate_plan(windows, cases)


def test_candidate_grid_cannot_change_risk_or_randomize():
    with pytest.raises(ValueError):
        validate_plan(
            (W,), (replace(CASES[0], overrides={"risk": {"risk_per_trade_pct": 2}}),)
        )
    with pytest.raises(ValueError):
        validate_plan((W,), (replace(CASES[0], seed=5),))
    with pytest.raises(ValueError):
        validate_plan(
            (W,),
            (
                replace(
                    CASES[0], overrides={"technical": {"min_adr_20d_pct": float("nan")}}
                ),
            ),
        )


def test_training_only_selection_durable_freeze_and_completed_resume(tmp_path):
    calls = []

    def execute(w, phase, cell, cfg):
        calls.append((phase, cell.case.name))
        if phase == "TEST":
            saved = json.loads((tmp_path / "report.json").read_text())
            assert saved["folds"][0]["status"] == "TEST_RUNNING"
            assert saved["folds"][0]["selection"]["selected_on"] == "TRAIN_ONLY"
        factor = 1.3 if cell.case == CASES[0] else 1.1
        return replay(cell, cfg, factor if phase == "TRAIN" else 0.9)

    r = runner(tmp_path, execute)
    report = r.run()
    row = report["folds"][0]
    assert len(calls) == 3 and calls[-1] == ("TEST", CASES[0].name)
    assert row["selection"]["candidate"]["name"] == CASES[0].name
    assert row["selection"]["config_hash"] == fingerprint(CASES[0].config(CFG))
    assert row["test_metrics"]["total_return_pct"] < 0
    assert report["summary"]["acceptance"] == "NOT_CERTIFIED"
    assert report["summary"]["performance_pass"] is None
    assert report["summary"]["computed_folds"] == 1
    assert (
        runner(tmp_path, lambda *a: pytest.fail("Completed test replayed")).run()
        == report
    )
    export_csv(report, tmp_path / "report.json")
    assert "oos_return_pct" in (tmp_path / "report.csv").read_text()


def test_bounded_work_resumes_training_then_freezes_then_tests(tmp_path):
    calls = []

    def execute(w, p, c, cfg):
        calls.append(p)
        return replay(c, cfg)

    r = runner(tmp_path, execute)
    assert r.run(max_cells=1)["folds"][0]["status"] == "TRAIN_PENDING"
    r = runner(tmp_path, execute)
    assert r.run(max_cells=1)["folds"][0]["status"] == "FROZEN"
    assert not any(p == "TEST" for p in calls)
    r = runner(tmp_path, execute)
    assert r.run(max_cells=1)["summary"]["status"] == "COMPLETED"
    assert calls == ["TRAIN", "TRAIN", "TEST"]


def test_partial_grid_is_never_selected_and_later_folds_do_not_run(tmp_path):
    calls = []

    def gate(w, p, c, cfg):
        calls.append((w.name, p, c.case.name))
        return ["missing_prices"] if c.case == CASES[1] else []

    report = runner(tmp_path, gate=gate).run()
    assert report["folds"][0]["blockers"] == ["training_grid_incomplete"]
    assert "selection" not in report["folds"][0]
    assert all(p == "TRAIN" for _, p, _ in calls)
    # Remaining valid candidates are not used to bypass a missing candidate.
    assert report["summary"]["computed_folds"] == 0


def test_minimum_closed_cycles_blocks_cash_only_or_too_small_training(tmp_path):
    r = runner(tmp_path)
    r.policy = SelectionPolicy(100)
    report = r.run()
    assert report["folds"][0]["blockers"] == ["insufficient_training_sample"]
    assert "selection" not in report["folds"][0]


def test_tie_break_is_independent_of_grid_order_and_no_infinite_score():
    rows = [
        dict(
            key=k,
            status="COMPLETED",
            case={"name": k},
            metrics={"closed_trades": 30, "cagr_pct": 10, "max_drawdown_pct": 5},
            evidence_hash=k,
        )
        for k in ("b", "a")
    ]
    assert select_candidate(rows, SelectionPolicy())[0]["candidate"]["name"] == "a"
    assert (
        select_candidate(list(reversed(rows)), SelectionPolicy())[0]["candidate"][
            "name"
        ]
        == "a"
    )
    rows[0]["metrics"]["cagr_pct"] = None
    rows[1]["metrics"]["closed_trades"] = 0
    assert (
        select_candidate(rows, SelectionPolicy())[1] == "insufficient_training_sample"
    )


def test_exception_redaction_and_retry_does_not_claim_oos_pass(tmp_path):
    def execute(*a):
        raise RuntimeError("postgres://user:password@secret-host")

    r = runner(tmp_path, execute)
    report = r.run()
    assert report["summary"]["status"] == "INCOMPLETE"
    assert "password" not in (tmp_path / "report.json").read_text()
    assert (
        "password"
        not in next((tmp_path / "report_folds").glob("*train.json")).read_text()
    )
    assert runner(tmp_path).run()["summary"]["status"] == "COMPLETED"


def test_runtime_data_gap_and_interrupt_resume(tmp_path):
    def gap(w, p, c, cfg):
        if p == "TEST":
            raise ResearchDataGap("held_price_missing")
        return replay(c, cfg)

    report = runner(tmp_path, gap).run()
    assert report["folds"][0]["status"] == "BLOCKED" and report["folds"][0][
        "blockers"
    ] == ["held_price_missing"]
    assert "test_metrics" not in report["folds"][0]

    def interrupt(w, p, c, cfg):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        runner(tmp_path, interrupt).run()
    assert runner(tmp_path).run()["summary"]["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "changed", ["checkpoint", "inner", "dataset", "code", "config", "journal"]
)
def test_evidence_changes_invalidate_resume(tmp_path, changed):
    def execute(w, p, c, cfg):
        result, diag = replay(c, cfg)
        folder = tmp_path / "report_folds" / "journals"
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / (c.key + ".json")
        file.write_text("original")
        diag.update(
            evidence_file="journals/" + file.name,
            evidence_sha256=hashlib.sha256(b"original").hexdigest(),
        )
        return result, diag

    r = runner(tmp_path, execute)
    r.run()
    args = dict(
        base=CFG,
        windows=(W,),
        candidates=CASES,
        datasets=deepcopy(DATA),
        path=tmp_path / "report.json",
        executor=execute,
        gate=lambda *a: [],
        code_version="test-code",
        policy=SelectionPolicy(1),
    )
    if changed == "checkpoint":
        p = tmp_path / "report.json"
        p.write_text(p.read_text().replace("TRAIN_ONLY", "TEST_ONLY"))
    elif changed == "inner":
        p = tmp_path / "report_folds" / "0_train.json"
        p.write_text(p.read_text() + " ")
    elif changed == "journal":
        next((tmp_path / "report_folds" / "journals").glob("*.json")).write_text(
            "changed"
        )
    elif changed == "dataset":
        args["datasets"][W.name]["TRAIN"]["fingerprint"] = "changed"
    elif changed == "code":
        args["code_version"] = "changed"
    else:
        args["base"] = deepcopy(CFG)
        args["base"]["risk"]["risk_per_trade_pct"] = 0.25
    with pytest.raises(ValueError):
        WalkForwardRunner(**args)


def test_preflight_manifests_are_cut_off_independently(monkeypatch):
    import packages.pms.walk_forward_data as data

    calls = []

    def manifest(snapshot, cells, symbols):
        calls.append(
            (min(c.period.start for c in cells), max(c.period.end for c in cells))
        )
        return dict(fingerprint=str(calls[-1]))

    monkeypatch.setattr(data, "input_manifest", manifest)
    datasets, gates = prepare_inputs(object(), (FINAL_WINDOW,), CASES)
    assert calls == [
        (FINAL_WINDOW.train_start, FINAL_WINDOW.train_end),
        (FINAL_WINDOW.test_start, FINAL_WINDOW.test_end),
    ]
    assert (
        datasets[FINAL_WINDOW.name]["TRAIN"]["fingerprint"]
        != datasets[FINAL_WINDOW.name]["TEST"]["fingerprint"]
    )


def test_persistence_is_research_only_and_identity_guarded(tmp_path):
    report = runner(tmp_path).run()
    engine = Engine([None])
    persist_walk_forward(engine, report)
    sql, params = engine.calls[0]
    assert (
        "pms.research_runs" in sql
        and "WALK_FORWARD_V2" in sql
        and "config->>'identity'" in sql
    )
    assert "orders" not in sql and "walk_forward_runs" not in sql
    assert json.loads(params["metrics"])["summary"]["acceptance"] == "NOT_CERTIFIED"


def test_api_saved_studies_are_read_only_paginated_and_separate(monkeypatch):
    engine = Engine([[dict(id="x", summary={})]])
    monkeypatch.setattr(walk_forward_routes, "_engine", lambda: engine)
    client = TestClient(app)
    assert client.get("/v1/research/walk-forward/studies").json()["count"] == 1
    assert "WALK_FORWARD_V2" in engine.calls[0][0]
    assert client.post("/v1/research/walk-forward/studies").status_code == 405
    assert client.get("/v1/research/walk-forward/studies?limit=101").status_code == 422
    assert client.get("/v1/research/walk-forward/studies/not-uuid").status_code == 422
    ident = "00000000-0000-4000-8000-000000000001"
    engine.values = [
        dict(
            id=ident,
            summary={"folds": 2},
            datasets={
                "W": {
                    "TRAIN": dict(
                        fingerprint="train", scope="SAMPLE", universe=["private-list"]
                    )
                }
            },
        ),
        [{"status": "FROZEN"}],
    ]
    response = client.get(f"/v1/research/walk-forward/studies/{ident}?page=2&limit=1")
    assert response.status_code == 200 and response.json()["page"] == 2
    assert "private-list" not in response.text
    assert engine.calls[-1][1]["offset"] == 1
    engine.values = [None]
    assert client.get(f"/v1/research/walk-forward/studies/{ident}").status_code == 404


def test_cli_plan_is_offline_and_final_holdout_requires_explicit_flag(capsys, tmp_path):
    assert main(["--plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert (
        plan["training_cells"] == 81
        and plan["test_cells"] == 3
        and not plan["final_holdout_included"]
    )
    assert main(["--plan", "--quick", "--include-holdout"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["training_cells"] == 16 and plan["test_cells"] == 4
    p = tmp_path / "windows.json"
    p.write_text(json.dumps(plain([asdict(FINAL_WINDOW)])))
    with pytest.raises(SystemExit):
        main(["--plan", "--windows", str(p)])


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--plan", "--run"],
        ["--plan", "--capital", "nan"],
        ["--plan", "--min-trades", "0"],
        ["--plan", "--max-cells", "0"],
    ],
)
def test_cli_invalid_modes_and_values(flags):
    with pytest.raises(SystemExit):
        main(flags)


def test_earlier_incomplete_fold_cannot_be_skipped_to_consume_holdout(tmp_path):
    windows = (W, FINAL_WINDOW)
    data = deepcopy(DATA)
    data[FINAL_WINDOW.name] = deepcopy(DATA[W.name])
    seen = []

    def gate(w, p, c, cfg):
        seen.append(w.name)
        return ["missing_history"]

    r = WalkForwardRunner(
        CFG,
        windows,
        CASES,
        data,
        tmp_path / "multi.json",
        executor=lambda *a: pytest.fail("Blocked replay started"),
        gate=gate,
        code_version="test-code",
        policy=SelectionPolicy(1),
    )
    report = r.run()
    assert set(seen) == {W.name}
    assert report["folds"][1]["status"] == "PENDING"
    assert report["summary"]["computed_folds"] == 0


def test_no_trade_test_is_recorded_without_performance_acceptance(tmp_path):
    def execute(w, p, c, cfg):
        result, diag = replay(c, cfg)
        if p == "TEST":
            result.fills.clear()
            result.events.clear()
        return result, diag

    report = runner(tmp_path, execute).run()
    assert report["folds"][0]["status"] == "NO_TRADES"
    assert report["summary"]["acceptance"] == "NOT_CERTIFIED"
    inner = json.loads((tmp_path / "report_folds" / "0_test.json").read_text())
    assert inner["design_scope"] == "WALK_FORWARD_PHASE"
    assert "2025 onward is reserved" not in inner["holdout"]


def test_executor_selects_phase_universe_and_saves_complete_local_journal(
    tmp_path, monkeypatch
):
    import packages.pms.walk_forward_data as data

    datasets = deepcopy(DATA)
    datasets[W.name]["TEST"]["universe"] = [dict(instrument_id="NSE:TEST_ONLY")]
    captured = []

    def execute(snapshot, symbols, cfg, case, start, end):
        captured.append((symbols, start, end))
        return replay(phase_cells(W, (case,), "TEST")[0], cfg)

    monkeypatch.setattr(data, "execute_replay", execute)
    cell = phase_cells(W, CASES[:1], "TEST")[0]
    result, diag = replay_executor(object(), datasets, tmp_path / "report.json")(
        W, "TEST", cell, cell.case.config(CFG)
    )
    assert captured == [(["NSE:TEST_ONLY"], W.test_start, W.test_end)]
    evidence = tmp_path / "report_folds" / diag["evidence_file"]
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == diag["evidence_sha256"]
    journal = json.loads(evidence.read_text())
    assert journal["fills"] and journal["events"] and journal["equity_curve"]
