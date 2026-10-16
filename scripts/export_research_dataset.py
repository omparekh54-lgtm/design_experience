from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date

from sqlalchemy import create_engine

from packages.pms.research_dataset import export_research_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Export immutable PMS research dataset to Parquet")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", default="research_data")
    parser.add_argument("--universe", default="NSE_EQUITY")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    manifest = export_research_dataset(engine, args.out, date.fromisoformat(args.start), date.fromisoformat(args.end), universe=args.universe)
    print(json.dumps(asdict(manifest), default=str, indent=2))


if __name__ == "__main__":
    main()
