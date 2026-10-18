from __future__ import annotations

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.pms.module_c_pipeline import ModuleCPipeline
from packages.pms.module_e_pipeline import ModuleEPipeline
from packages.pms.portfolio_accounting import PortfolioAccountingService

IST = ZoneInfo("Asia/Kolkata")


def engine_from_env():
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    return create_engine(_render_safe_database_url(raw), pool_pre_ping=True,
                         connect_args={'options':'-c search_path=pms,public'})


def latest_price_date(engine) -> date | None:
    with engine.connect() as conn:
        return conn.execute(text("select max(trade_date) from daily_prices")).scalar_one_or_none()


def latest_benchmark_dates(engine) -> dict[str, date | None]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                select benchmark_code,max(trade_date) latest
                from module_e_benchmark_bars
                where benchmark_code in ('NIFTYMIDSMALLCAP400','INDIAVIX')
                group by benchmark_code
                """
            )
        ).mappings().all()
    out: dict[str, date | None] = {"NIFTYMIDSMALLCAP400": None, "INDIAVIX": None}
    for row in rows:
        out[str(row["benchmark_code"])] = row["latest"]
    return out


def unresolved_errors(engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("select count(*) from data_quality_issues where severity='ERROR' and resolved_at is null")
            ).scalar_one()
        )


def run(trade_date: date | None = None) -> dict:
    engine = engine_from_env()
    d = trade_date or datetime.now(IST).date()

    if d.weekday() >= 5:
        return {"status": "SKIPPED", "reason": "weekend", "trade_date": str(d)}

    price_date = latest_price_date(engine)
    if price_date != d:
        return {
            "status": "SKIPPED",
            "reason": "market_prices_not_ready_or_holiday",
            "trade_date": str(d),
            "latest_price_date": str(price_date) if price_date else None,
        }

    benchmark_dates = latest_benchmark_dates(engine)
    stale = {
        code: value
        for code, value in benchmark_dates.items()
        if value is None or (d - value).days > 5
    }
    if stale:
        return {
            "status": "SKIPPED",
            "reason": "module_e_benchmarks_not_ready",
            "trade_date": str(d),
            "benchmark_dates": {k: str(v) if v else None for k, v in benchmark_dates.items()},
        }

    errors = unresolved_errors(engine)
    if errors:
        return {
            "status": "SKIPPED",
            "reason": "unresolved_data_quality_errors",
            "trade_date": str(d),
            "errors": errors,
        }

    _, regime = ModuleEPipeline(engine).evaluate(d, persist=True)
    accounting = PortfolioAccountingService(engine)
    snapshot = accounting.snapshot(d, "PAPER")
    accounting.persist_snapshot(snapshot)
    module_c = ModuleCPipeline(engine).generate(d)

    return {
        "status": "SUCCESS",
        "trade_date": str(d),
        "mode": "PAPER",
        "regime": regime.band,
        "capacity": regime.max_open_trades,
        "module_c": {
            "universe_size": module_c.universe_size,
            "a_eligible": module_c.a_eligible,
            "b_signals": module_c.b_signals,
            "ranked": module_c.ranked,
            "qualified": module_c.qualified,
            "selected": module_c.selected,
            "rejected": module_c.rejected,
        },
        "paper_nav": snapshot.nav,
        "broker_orders_submitted": 0,
    }


def main() -> None:
    result = run()
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
