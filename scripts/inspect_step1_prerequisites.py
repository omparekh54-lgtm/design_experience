"""Read-only report of the exact schema used by the local Step-1 finalizer.

Never applies migrations, starts ingestion, changes certificates or prints URLs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_storage import CloudStore
from packages.pms.data_integrity import SCHEMA_SQL, schema_findings
from packages.pms.matrix_data import consistent_inputs


def inspect(engine):
    with consistent_inputs(engine) as snapshot:
        identity = dict(snapshot.execute(text(
            "select current_database() database_name,current_user database_role"
        )).mappings().one())
        layout = CloudStore(snapshot).inspect_tables()
        missing = schema_findings(snapshot.execute(text(SCHEMA_SQL)).mappings().all())
    return {"status": "PASS" if layout["status"] == "PASS" and not missing else "BLOCKED",
            "database": identity, "writable_layout": layout, "missing_schema": missing,
            "read_only": True, "migrations_applied": False}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        print("STOPPED: DATABASE_URL or PMS_DATABASE_URL is required in .env", file=sys.stderr)
        return 2
    engine = create_engine(_render_safe_database_url(raw), pool_pre_ping=True,
                           connect_args={"options": "-c search_path=pms,public"})
    try:
        report = inspect(engine)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["status"] == "PASS" else 2
    except Exception as exc:
        print(f"STOPPED: {type(exc).__name__}: schema inspection failed; connection details withheld", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
