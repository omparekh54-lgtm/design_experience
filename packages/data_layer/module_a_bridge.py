from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from packages.pms.fundamental_history import FinancialHistoryMetrics, calculate_financial_history
from packages.pms.module_a import FundamentalDecision, FundamentalSnapshot, evaluate_fundamentals

from .queries import PointInTimeQueries


@dataclass(frozen=True)
class ModuleAHardFilterContext:
    market_cap_cr: float
    avg_traded_value_cr: float
    promoter_pledge_pct: float


@dataclass(frozen=True)
class ModuleAHistoryEvaluation:
    instrument_id: str
    as_of: datetime
    metrics: FinancialHistoryMetrics
    snapshot: FundamentalSnapshot
    decision: FundamentalDecision
    history: tuple[dict[str, Any], ...]


class ModuleAHistoryService:
    """Point-in-time bridge from exchange filings to deterministic Module A logic."""

    def __init__(self, engine: Engine, cfg: dict):
        self.queries = PointInTimeQueries(engine)
        self.cfg = cfg

    def evaluate(
        self,
        instrument_id: str,
        as_of: datetime,
        hard_filters: ModuleAHardFilterContext,
    ) -> ModuleAHistoryEvaluation:
        history = self.queries.financial_history_as_of(
            instrument_id,
            as_of,
            prefer_consolidated=True,
            limit_periods=20,
        )
        if not history:
            raise ValueError(f"no financial history available for {instrument_id} as of {as_of.isoformat()}")

        fcfg = self.cfg["fundamental"]
        consistency = tuple(fcfg.get("consistency_growth_metrics", ("sales", "pat")))
        metrics = calculate_financial_history(
            history,
            consistency_metrics=consistency,
            min_ocf_to_pat=float(fcfg["min_ocf_to_pat"]),
        )
        surveillance = self.queries.surveillance_as_of(instrument_id, as_of)

        snapshot = FundamentalSnapshot(
            market_cap_cr=hard_filters.market_cap_cr,
            avg_traded_value_cr=hard_filters.avg_traded_value_cr,
            asm_gsm=bool(surveillance["asm"] or surveillance["gsm"]),
            promoter_pledge_pct=hard_filters.promoter_pledge_pct,
            quarterly_sales_cr=metrics.quarterly_sales_cr,
            pat_cr=metrics.pat_cr,
            sales_yoy_pct=metrics.sales_yoy_pct,
            operating_profit_yoy_pct=metrics.operating_profit_yoy_pct,
            pat_yoy_pct=metrics.pat_yoy_pct,
            eps_yoy_pct=metrics.eps_yoy_pct,
            positive_growth_quarters_last_4=metrics.positive_growth_quarters_last_4,
            ttm_sales_rising=metrics.ttm_sales_rising,
            ttm_profit_rising=metrics.ttm_profit_rising,
            ocf_to_pat=metrics.ocf_to_pat,
            quality_pass=metrics.quality_pass,
            growth_acceleration_pp=metrics.growth_acceleration_pp,
            margin_expansion_bps=metrics.margin_expansion_bps,
            debt_to_equity=metrics.debt_to_equity,
            interest_coverage=metrics.interest_coverage,
            record_sales_8q=metrics.record_sales_8q,
            record_pat_8q=metrics.record_pat_8q,
            turnaround=metrics.turnaround,
            triggers=metrics.triggers,
            flags=metrics.flags,
        )
        decision = evaluate_fundamentals(snapshot, self.cfg)
        return ModuleAHistoryEvaluation(
            instrument_id=instrument_id,
            as_of=as_of,
            metrics=metrics,
            snapshot=snapshot,
            decision=decision,
            history=tuple(history),
        )
