from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from types import SimpleNamespace
import json

import pytest

from packages.pms import replay_service as service
from packages.pms.backtest import EventDrivenBacktester, MarketBar, Signal
from packages.pms.backtest_matrix import Case, Cell, Period, metrics
from packages.pms.config import load_strategy_config
from packages.pms.database_backtest import DatabaseBacktestStrategy
from packages.pms.matrix_replay import ResearchC, ResearchDataGap
from packages.pms.module_b_pipeline import ModuleBResult
from packages.pms.module_c_pipeline import ModuleCPipeline
from packages.pms.research import ResearchVariant
from packages.pms.research_lab import ResearchLab

START = date(2020, 2, 3)
END = START + timedelta(days=3)
VARIANT = ResearchVariant("FULL_A_B_C_E")
CASE = Case(VARIANT.name, VARIANT)


@pytest.fixture
def prepared(monkeypatch):
    trace = SimpleNamespace(snapshots=0, manifests=0, runs=[], gates=[], blockers=[])
    @contextmanager
    def snapshot(engine):
        trace.snapshots += 1
        yield "READ_ONLY_SNAPSHOT"
    def manifest(snapshot, cells, symbols):
        trace.manifests += 1
        assert snapshot == "READ_ONLY_SNAPSHOT"
        assert symbols == ["NSE:A"]
        return dict(universe=[dict(instrument_id="NSE:A")], fingerprint="dataset", scope="EXPLICIT_SYMBOL_SAMPLE")
    class Gate:
        def __init__(self, *a): pass
        def __call__(self, cell, cfg):
            trace.gates.append((cell, deepcopy(cfg)))
            return trace.blockers
    class TapeReplay:
        def __init__(self, snapshot, symbols, cfg, case, start, end):
            trace.runs.append((snapshot, symbols, deepcopy(cfg), case, start, end))
            self.cfg, self.start, self.end = cfg, start, end
        def run(self):
            b = EventDrivenBacktester(cfg=self.cfg)
            days = [self.start + timedelta(days=i) for i in range((self.end-self.start).days+1)]
            bars = [MarketBar("NSE:A", d, 100, 102, 96, 101, 100, 100, 99) for d in days]
            bars[-1] = MarketBar("NSE:A", days[-1], 94, 95, 90, 91, 96, 98, 97)
            result = b.run(bars, lambda d: [Signal("NSE:A", self.start, 100, 95)] if d == self.start else [])
            # A replay may mutate its private config, never the caller's.
            self.cfg["risk"]["max_open_trades"] = 0
            return result, {"source": "synthetic_tape"}
    monkeypatch.setattr(service, "consistent_inputs", snapshot)
    monkeypatch.setattr(service, "input_manifest", manifest)
    monkeypatch.setattr(service, "MatrixGates", Gate)
    monkeypatch.setattr(service, "MatrixReplay", TapeReplay)
    monkeypatch.setattr(service, "code_fingerprint", lambda: "code")
    return trace


def test_adapter_lab_matrix_and_walk_forward_execute_identical_tape(prepared, tmp_path):
    from packages.pms.walk_forward_data import replay_executor
    cfg = load_strategy_config()
    original = deepcopy(cfg)
    adapter = DatabaseBacktestStrategy(object(), ["nse:a", "NSE:A"], cfg=cfg)
    single = adapter.run(START, END)
    lab = ResearchLab(object(), ["NSE:A"], START, END, cfg=cfg)
    lab_result = lab.run_variant(cfg, VARIANT)
    cell = Cell(Period("CUSTOM", START, END), CASE)
    matrix, matrix_diag = service.execute_replay("READ_ONLY_SNAPSHOT", ["NSE:A"], cfg, CASE, START, END)
    window = SimpleNamespace(name="W")
    datasets = {"W": {"TEST": {"universe": [dict(instrument_id="NSE:A")]}}}
    walk, walk_diag = replay_executor("READ_ONLY_SNAPSHOT", datasets, tmp_path / "walk.json")(
        window, "TEST", cell, cfg)
    assert asdict(single) == asdict(matrix) == asdict(walk)
    expected = metrics(single, cell.period)
    assert all(lab_result[k] == v for k, v in expected.items())
    identities = [adapter.diagnostics, lab_result["diagnostics"], matrix_diag, walk_diag]
    for key in ("engine", "strategy_version", "config_fingerprint", "policy_fingerprint", "result_fingerprint"):
        assert len({d[key] for d in identities}) == 1
    assert adapter.diagnostics["dataset_fingerprint"] == "dataset"
    assert cfg == original
    normalized = deepcopy(original)
    normalized["backtest"]["matrix_research_only"] = True
    assert adapter.diagnostics["config_fingerprint"] == service.fingerprint(normalized)


def test_all_variants_share_one_snapshot_but_start_fresh_accounts(prepared):
    variants = [ResearchVariant("A"), ResearchVariant("B", reentry=False)]
    lab = ResearchLab(object(), ["NSE:A"], START, END, initial_capital=250000)
    original = deepcopy(lab.base_config)
    rows = lab.run_all_variants(variants)
    assert prepared.snapshots == prepared.manifests == 1
    assert len(prepared.runs) == len(prepared.gates) == 2
    assert all(r["initial_capital"] == 250000 for r in rows.values())
    assert lab.base_config == original
    assert all(r[2]["risk"]["max_open_trades"] > 0 for r in prepared.runs)


def test_robustness_uses_one_snapshot_and_all_distinct_configurations(prepared):
    lab = ResearchLab(object(), ["NSE:A"], START, END)
    rows = lab.run_robustness()
    assert len(rows) == len(prepared.runs) == 81
    assert prepared.snapshots == prepared.manifests == 1
    assert len({r.metrics["diagnostics"]["config_fingerprint"] for r in rows}) == 81


@pytest.mark.parametrize("command", ["adapter", "variants", "robustness"])
def test_missing_inputs_block_before_any_replay(prepared, command):
    prepared.blockers = ["historical_price_sessions_missing_invalid_or_adjustment_unverified"]
    with pytest.raises(ResearchDataGap, match="historical_price_sessions"):
        if command == "adapter":
            DatabaseBacktestStrategy(object(), ["NSE:A"]).run(START, END)
        elif command == "variants":
            ResearchLab(object(), ["NSE:A"], START, END).run_all_variants([VARIANT])
        else:
            ResearchLab(object(), ["NSE:A"], START, END).run_robustness()
    assert not prepared.runs


@pytest.mark.parametrize("capital", [0, -1, float("nan"), float("inf")])
def test_invalid_capital_cannot_reach_execution(prepared, capital):
    cfg = load_strategy_config()
    cfg["backtest"]["initial_capital"] = capital
    with pytest.raises(ValueError, match="capital"):
        service.execute_replay(object(), ["NSE:A"], cfg, CASE, START, END)
    assert not prepared.runs


@pytest.mark.parametrize("mode", ["variants", "robustness"])
def test_optimization_preserves_reserved_holdout(prepared, mode):
    lab = ResearchLab(object(), ["NSE:A"], START, date(2025, 1, 2))
    with pytest.raises(ValueError, match="reserved holdout"):
        lab.run_all_variants() if mode == "variants" else lab.run_robustness()
    assert not prepared.runs and prepared.snapshots == 0


def test_exploratory_holdout_single_run_is_explicitly_uncertified(prepared):
    adapter = DatabaseBacktestStrategy(object(), ["NSE:A"])
    adapter.run(date(2025, 1, 6), date(2025, 1, 9))
    assert adapter.diagnostics["period_kind"] == "EXPLORATORY_HOLDOUT"
    assert adapter.diagnostics["acceptance"] == "NOT_CERTIFIED"


def test_setup2_is_evaluated_only_in_research_c_and_remains_blocked_in_production():
    calls = []
    result = ModuleBResult("NSE:A", START, True, (),
                          dict(setup="SETUP_2", status="ACTIVE"), research_only=True)
    research = object.__new__(ResearchC)
    research.replay = SimpleNamespace(case=SimpleNamespace(variant=ResearchVariant("S2", setup_2=True)),
        setup2=SimpleNamespace(evaluate=lambda *a, **kw: calls.append((a, kw)) or result))
    assert research._evaluate_signal("NSE:A", START) is result
    research._validate_signal(result)
    assert calls[0][1] == dict(require_module_a=True)
    production = object.__new__(ModuleCPipeline)
    with pytest.raises(ValueError, match="research_signal_not_production"):
        production._validate_signal(result)


def test_single_cli_uses_real_applied_capital_and_persists_execution_identity(prepared, monkeypatch, capsys):
    from scripts import run_backtest as cli
    calls = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None))
    class Persistence:
        def __init__(self, *a): pass
        def create_run(self, run_id, **kwargs): calls.append(kwargs)
        def persist_result(self, *a): pass
        def finalize_run(self, *a): pass
    monkeypatch.setattr(cli, "BacktestPersistence", Persistence)
    cli.main(["--symbols", "NSE:A", "--start", str(START), "--end", str(END), "--capital", "250000"])
    output = json.loads(capsys.readouterr().out)
    assert output["initial_capital"] == calls[0]["config"]["backtest"]["initial_capital"] == 250000
    assert calls[0]["config"]["research_execution"] == output["diagnostics"]
    assert output["metrics"]["total_return_pct"] == output["return_pct"] or abs(
        output["metrics"]["total_return_pct"] - output["return_pct"]) < 0.0001


@pytest.mark.parametrize("command", ["backtest", "lab"])
def test_cli_reports_data_gap_and_never_persists_success(prepared, monkeypatch, capsys, command):
    from scripts import run_backtest, run_research_lab
    cli = run_backtest if command == "backtest" else run_research_lab
    prepared.blockers = ["missing_inputs"]
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None))
    if command == "backtest":
        monkeypatch.setattr(cli, "BacktestPersistence", lambda *a: pytest.fail("Blocked replay must not persist"))
    assert cli.main(["--symbols", "NSE:A", "--start", str(START), "--end", str(END)]) == 2
    assert json.loads(capsys.readouterr().out) == dict(status="BLOCKED", blockers=["missing_inputs"])
    assert not prepared.runs


def test_matrix_command_matches_single_run_metrics_and_execution_identity(prepared, monkeypatch, tmp_path, capsys):
    from packages.pms import matrix_data
    from scripts import run_backtest_matrix as cli
    import sqlalchemy
    for name in ("consistent_inputs", "input_manifest", "MatrixGates", "code_fingerprint"):
        monkeypatch.setattr(matrix_data, name, getattr(service, name))
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None))
    cell = Cell(Period("CUSTOM", START, END), CASE)
    monkeypatch.setattr(cli, "matrix_plan", lambda *a, **kw: [cell])
    report = tmp_path / "matrix.json"
    cli.main(["--run", "--symbols", "NSE:A", "--start", str(START), "--end", str(END),
              "--report", str(report), "--no-persist"])
    capsys.readouterr()
    row = json.loads(report.read_text())["cells"][0]
    adapter = DatabaseBacktestStrategy(object(), ["NSE:A"])
    result = adapter.run(START, END)
    assert row["metrics"] == metrics(result, cell.period)
    for key in ("engine", "strategy_version", "policy_fingerprint", "config_fingerprint", "result_fingerprint"):
        assert row["diagnostics"][key] == adapter.diagnostics[key]
