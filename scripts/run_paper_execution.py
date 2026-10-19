"""Formal forward PAPER settlement/admission. No real broker orders."""

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.run_market_close import engine_from_env
from packages.pms.formal_paper import FormalPaperExecution


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["settle", "admit"])
    args = parser.parse_args(argv)
    engine = engine_from_env()
    try:
        service = FormalPaperExecution(engine)
        result = service.settle() if args.action == "settle" else service.admit()
        print(json.dumps(result, default=str, indent=2, allow_nan=False))
        return 2 if result["status"] == "BLOCKED" else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
