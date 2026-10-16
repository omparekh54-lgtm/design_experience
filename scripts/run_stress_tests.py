from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import create_engine

from packages.pms.research import ResearchVariant
from packages.pms.stress_testing import StressTestRunner, summarize_stress_results
from packages.pms.validation_persistence import ValidationPersistence


def main() -> None:
    parser = argparse.ArgumentParser(description="Run predefined PMS stress-period backtests")
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical IDs")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--variant", default="FULL_A_B_C_E")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    variant = ResearchVariant(args.variant)
    results = StressTestRunner(engine, symbols, args.capital).run(variant=variant)
    summary = summarize_stress_results(results)

    run_id = uuid4()
    if not args.no_persist:
        ValidationPersistence(engine).save_stress_run(run_id, variant=variant.name, symbols=symbols, initial_capital=args.capital, results=results, summary=summary)

    print(json.dumps({
        "run_id": None if args.no_persist else str(run_id),
        "summary": summary,
        "results": [{"period": asdict(r.period), "metrics": r.metrics} for r in results],
    }, default=str, indent=2))


if __name__ == "__main__":
    main()
