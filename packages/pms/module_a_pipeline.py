from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import Engine

from packages.data_layer.queries import PointInTimeQueries
from packages.data_layer.reference_data import ReferenceDataQueries
from packages.pms.config import load_strategy_config
from packages.pms.fundamental_history import calculate_financial_history
from packages.pms.module_a import FundamentalDecision, FundamentalSnapshot, evaluate_fundamentals


class ModuleAPipeline:
    """Fully point-in-time Module A evaluation from stored exchange/market data."""

    def __init__(self, engine: Engine):
        self.financials = PointInTimeQueries(engine)
        self.reference = ReferenceDataQueries(engine)

    def evaluate(self, instrument_id: str, as_of: datetime) -> tuple[FundamentalSnapshot, FundamentalDecision]:
        cfg = load_strategy_config()
        history = self.financials.financial_history_as_of(instrument_id, as_of, limit_periods=12)
        if not history:
            raise ValueError(f"no financial history available for {instrument_id} at {as_of.isoformat()}")
        metrics = calculate_financial_history(
            history,
            consistency_metrics=tuple(cfg["fundamental"].get("consistency_growth_metrics", ["sales", "pat"])),
            min_ocf_to_pat=float(cfg["fundamental"]["min_ocf_to_pat"]),
        )
        shareholding = self.reference.shareholding_as_of(instrument_id, as_of)
        market_cap = self.reference.market_cap_as_of(instrument_id, as_of)
        traded_value = self.reference.avg_traded_value_cr(instrument_id, as_of)
        surveillance = self.financials.surveillance_as_of(instrument_id, as_of)

        flags = list(metrics.flags)
        if market_cap is None:
            flags.append("market_cap_unavailable")
        if traded_value is None:
            flags.append("avg_traded_value_unavailable")
        if shareholding is None:
            flags.append("shareholding_unavailable")
        pledge = float(shareholding["promoter_pledge_pct"]) if shareholding and shareholding.get("promoter_pledge_pct") is not None else None
        if pledge is None:
            flags.append("promoter_pledge_unavailable")

        # Missing hard-filter data is represented by a deterministic failing value
        # plus an explicit flag; it never silently passes eligibility.
        snapshot = FundamentalSnapshot(
            market_cap_cr=float(market_cap or 0.0),
            avg_traded_value_cr=float(traded_value or 0.0),
            asm_gsm=bool(surveillance.get("asm") or surveillance.get("gsm")),
            promoter_pledge_pct=float(pledge if pledge is not None else 100.0),
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
            flags=tuple(dict.fromkeys(flags)),
        )
        return snapshot, evaluate_fundamentals(snapshot, cfg)

    @staticmethod
    def serialize(snapshot: FundamentalSnapshot, decision: FundamentalDecision) -> dict:
        return {
            "snapshot": asdict(snapshot),
            "decision": {
                "eligible": decision.eligible,
                "score": decision.score,
                "failed_rules": list(decision.failed_rules),
                "flags": list(decision.flags),
                "triggers": list(decision.triggers),
                "components": decision.components,
            },
        }
