from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from sqlalchemy import create_engine

from packages.data_layer.module_a_bridge import ModuleAHardFilterContext, ModuleAHistoryService
from packages.pms.config import load_strategy_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate Module A from point-in-time official exchange financial history."
    )
    parser.add_argument("--instrument", required=True, help="Stable ID, e.g. NSE:RELIANCE")
    parser.add_argument("--as-of", required=True, help="ISO timestamp, e.g. 2026-10-16T18:00:00")
    parser.add_argument("--market-cap-cr", required=True, type=float)
    parser.add_argument("--avg-traded-value-cr", required=True, type=float)
    parser.add_argument("--promoter-pledge-pct", required=True, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    as_of = datetime.fromisoformat(args.as_of)
    service = ModuleAHistoryService(create_engine(database_url, pool_pre_ping=True), load_strategy_config())
    result = service.evaluate(
        args.instrument,
        as_of,
        ModuleAHardFilterContext(
            market_cap_cr=args.market_cap_cr,
            avg_traded_value_cr=args.avg_traded_value_cr,
            promoter_pledge_pct=args.promoter_pledge_pct,
        ),
    )

    print(
        json.dumps(
            {
                "instrument_id": result.instrument_id,
                "as_of": result.as_of.isoformat(),
                "eligible": result.decision.eligible,
                "score": result.decision.score,
                "score_components": result.decision.components,
                "failed_rules": result.decision.failed_rules,
                "triggers": result.decision.triggers,
                "flags": result.decision.flags,
                "financial_metrics": {
                    "quarterly_sales_cr": result.metrics.quarterly_sales_cr,
                    "pat_cr": result.metrics.pat_cr,
                    "sales_yoy_pct": result.metrics.sales_yoy_pct,
                    "operating_profit_yoy_pct": result.metrics.operating_profit_yoy_pct,
                    "pat_yoy_pct": result.metrics.pat_yoy_pct,
                    "eps_yoy_pct": result.metrics.eps_yoy_pct,
                    "positive_growth_quarters_last_4": result.metrics.positive_growth_quarters_last_4,
                    "ttm_sales_rising": result.metrics.ttm_sales_rising,
                    "ttm_profit_rising": result.metrics.ttm_profit_rising,
                    "ocf_to_pat": result.metrics.ocf_to_pat,
                    "growth_acceleration_pp": result.metrics.growth_acceleration_pp,
                    "margin_expansion_bps": result.metrics.margin_expansion_bps,
                    "debt_to_equity": result.metrics.debt_to_equity,
                    "interest_coverage": result.metrics.interest_coverage,
                },
            },
            indent=2,
            default=str,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
