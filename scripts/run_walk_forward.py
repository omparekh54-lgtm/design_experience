from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import create_engine

from packages.pms.research import ResearchVariant
from packages.pms.validation_persistence import ValidationPersistence
from packages.pms.walk_forward import WalkForwardRunner, summarize_walk_forward


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PMS walk-forward/out-of-sample validation")
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical IDs")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--variant", default="FULL_A_B_C_E")
    parser.add_argument("--quick", action="store_true", help="Use a reduced 2x2x1x1 train grid for smoke/research runs")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    variant = ResearchVariant(args.variant)
    kwargs = {}
    if args.quick:
        kwargs = {"adr_values": (2.5, 3.0), "rally_values": (12.0, 15.0), "risk_values": (0.5,), "volume_values": (1.5,)}
    results = WalkForwardRunner(engine, symbols, args.capital).run(variant=variant, **kwargs)
    summary = summarize_walk_forward(results)

    run_id = uuid4()
    if not args.no_persist:
        ValidationPersistence(engine).save_walk_forward_run(run_id, variant=variant.name, symbols=symbols, initial_capital=args.capital, results=results, summary=summary)

    print(json.dumps({
        "run_id": None if args.no_persist else str(run_id),
        "summary": summary,
        "results": [
            {"window": asdict(r.window), "selected_parameters": asdict(r.parameters), "train_metrics": r.train_metrics, "test_metrics": r.test_metrics}
            for r in results
        ],
    }, default=str, indent=2))


if __name__ == "__main__":
    main()
