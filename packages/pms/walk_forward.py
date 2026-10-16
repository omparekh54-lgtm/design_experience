from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import Iterable

from packages.pms.config import load_strategy_config
from packages.pms.research import ResearchVariant
from packages.pms.research_lab import ResearchLab


@dataclass(frozen=True)
class WalkForwardWindow:
    name: str
    train_start: date
    train_end: date
    test_start: date
    test_end: date


DEFAULT_WINDOWS: tuple[WalkForwardWindow, ...] = (
    WalkForwardWindow("WF_1", date(2014, 1, 1), date(2018, 12, 31), date(2019, 1, 1), date(2020, 12, 31)),
    WalkForwardWindow("WF_2", date(2015, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2022, 12, 31)),
    WalkForwardWindow("WF_3", date(2017, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2024, 12, 31)),
    WalkForwardWindow("HOLDOUT", date(2017, 1, 1), date(2024, 12, 31), date(2025, 1, 1), date(2026, 10, 16)),
)


@dataclass(frozen=True)
class SelectedParameters:
    adr: float
    rally: float
    risk: float
    volume_ratio: float
    training_score: float


@dataclass(frozen=True)
class WalkForwardResult:
    window: WalkForwardWindow
    parameters: SelectedParameters
    train_metrics: dict
    test_metrics: dict


def _objective(metrics: dict) -> float:
    total_return = float(metrics.get("total_return_pct", 0.0))
    max_dd = float(metrics.get("max_drawdown_pct", 0.0))
    pf = float(metrics.get("profit_factor") or 0.0)
    return total_return + min(pf, 3.0) * 10.0 - max_dd


def _with_params(base: dict, *, adr: float, rally: float, risk: float, volume: float) -> dict:
    return {
        **base,
        "technical": {
            **base["technical"],
            "min_adr_20d_pct": adr,
            "rally_min_pct": rally,
            "rally_min_volume_ratio": volume,
        },
        "risk": {**base["risk"], "risk_per_trade_pct": risk},
    }


class WalkForwardRunner:
    def __init__(self, engine, symbols: list[str], initial_capital: float | None = None):
        self.engine = engine
        self.symbols = symbols
        self.initial_capital = initial_capital
        self.base_config = load_strategy_config()

    def _select_on_train(
        self,
        window: WalkForwardWindow,
        variant: ResearchVariant,
        *,
        adr_values: tuple[float, ...],
        rally_values: tuple[float, ...],
        risk_values: tuple[float, ...],
        volume_values: tuple[float, ...],
    ) -> tuple[SelectedParameters, dict, dict]:
        lab = ResearchLab(self.engine, self.symbols, window.train_start, window.train_end, self.initial_capital)
        best: tuple[float, dict, dict, tuple[float, float, float, float]] | None = None
        for adr, rally, risk, volume in product(adr_values, rally_values, risk_values, volume_values):
            cfg = _with_params(self.base_config, adr=adr, rally=rally, risk=risk, volume=volume)
            metrics = lab.run_variant(cfg, variant)
            score = _objective(metrics)
            if best is None or score > best[0]:
                best = (score, metrics, cfg, (adr, rally, risk, volume))
        if best is None:
            raise RuntimeError("walk-forward grid is empty")
        score, metrics, cfg, params = best
        selected = SelectedParameters(params[0], params[1], params[2], params[3], round(score, 4))
        return selected, metrics, cfg

    def run_window(
        self,
        window: WalkForwardWindow,
        *,
        variant: ResearchVariant | None = None,
        adr_values: tuple[float, ...] = (2.5, 3.0, 4.0),
        rally_values: tuple[float, ...] = (12.0, 15.0, 18.0),
        risk_values: tuple[float, ...] = (0.25, 0.5, 0.75),
        volume_values: tuple[float, ...] = (1.25, 1.5, 2.0),
    ) -> WalkForwardResult:
        variant = variant or ResearchVariant("FULL_A_B_C_E")
        selected, train_metrics, frozen_cfg = self._select_on_train(
            window,
            variant,
            adr_values=adr_values,
            rally_values=rally_values,
            risk_values=risk_values,
            volume_values=volume_values,
        )
        # The test period is evaluated exactly once using parameters selected only on train data.
        test_lab = ResearchLab(self.engine, self.symbols, window.test_start, window.test_end, self.initial_capital)
        test_metrics = test_lab.run_variant(frozen_cfg, variant)
        return WalkForwardResult(window, selected, train_metrics, test_metrics)

    def run(
        self,
        windows: Iterable[WalkForwardWindow] = DEFAULT_WINDOWS,
        *,
        variant: ResearchVariant | None = None,
        adr_values: tuple[float, ...] = (2.5, 3.0, 4.0),
        rally_values: tuple[float, ...] = (12.0, 15.0, 18.0),
        risk_values: tuple[float, ...] = (0.25, 0.5, 0.75),
        volume_values: tuple[float, ...] = (1.25, 1.5, 2.0),
    ) -> list[WalkForwardResult]:
        return [
            self.run_window(
                window,
                variant=variant,
                adr_values=adr_values,
                rally_values=rally_values,
                risk_values=risk_values,
                volume_values=volume_values,
            )
            for window in windows
        ]


def summarize_walk_forward(results: Iterable[WalkForwardResult]) -> dict:
    rows = list(results)
    if not rows:
        return {"windows": 0, "positive_oos_windows": 0, "average_oos_return_pct": 0.0, "worst_oos_drawdown_pct": 0.0}
    test_returns = [float(r.test_metrics.get("total_return_pct", 0.0)) for r in rows]
    test_dd = [float(r.test_metrics.get("max_drawdown_pct", 0.0)) for r in rows]
    return {
        "windows": len(rows),
        "positive_oos_windows": sum(1 for x in test_returns if x > 0),
        "average_oos_return_pct": round(sum(test_returns) / len(test_returns), 4),
        "worst_oos_drawdown_pct": max(test_dd) if test_dd else 0.0,
    }
