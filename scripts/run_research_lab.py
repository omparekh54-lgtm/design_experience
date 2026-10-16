from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date

from sqlalchemy import create_engine

from packages.pms.research import ResearchVariant
from packages.pms.research_lab import ResearchLab


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PMS research variants or robustness grid")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--mode", choices=["variants", "robustness"], default="variants")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    lab = ResearchLab(engine, symbols, date.fromisoformat(args.start), date.fromisoformat(args.end), args.capital)

    if args.mode == "variants":
        result = lab.run_all_variants()
    else:
        result = [asdict(x) for x in lab.run_robustness(ResearchVariant("FULL_A_B_C_E"))]
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
