from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date
from uuid import uuid4

from sqlalchemy import create_engine

from packages.pms.backtest_persistence import BacktestPersistence
from packages.pms.config import load_strategy_config
from packages.pms.database_backtest import DatabaseBacktestStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Run point-in-time PMS backtest using production Module A/B/trade logic")
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical IDs, e.g. NSE:ABC,NSE:XYZ")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url, pool_pre_ping=True)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    cfg = load_strategy_config()

    strategy = DatabaseBacktestStrategy(engine, symbols)
    result = strategy.run(start, end, initial_capital=args.capital)

    run_id = uuid4()
    if not args.no_persist:
        persistence = BacktestPersistence(engine)
        persistence.create_run(
            run_id,
            strategy_version=cfg["version"],
            start=start,
            end=end,
            initial_capital=result.initial_capital,
            config=cfg,
        )
        persistence.persist_result(run_id, result)
        persistence.finalize_run(run_id, result)

    print(json.dumps({
        "run_id": None if args.no_persist else str(run_id),
        "initial_capital": result.initial_capital,
        "ending_cash": result.ending_cash,
        "realized_gross_pnl": result.realized_gross_pnl,
        "total_costs": result.total_costs,
        "realized_net_pnl": result.realized_net_pnl,
        "return_pct": result.return_pct,
        "closed_trades": result.closed_trades,
        "fills": [asdict(fill) for fill in result.fills],
    }, default=str, indent=2))


if __name__ == "__main__":
    main()
