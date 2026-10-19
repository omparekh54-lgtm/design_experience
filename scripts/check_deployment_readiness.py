"""Phase-1 deployment readiness; explicit optional read-only database inspection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.pms.config import load_strategy_config
from packages.pms.deployment_readiness import assess, check, database_checks


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--check", action="store_true")
    p.add_argument(
        "--database",
        action="store_true",
        help="Explicit read-only schema/safety/DQ probe",
    )
    p.add_argument(
        "--output", type=Path, help="Write new JSON evidence; refuses overwrite"
    )
    a = p.parse_args(argv)
    if a.database and not a.check:
        p.error("--database requires --check")
    if a.output and a.output.exists():
        p.error("Output already exists; preserve previous evidence")
    if a.check:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    db = None
    if a.database:
        engine = None
        try:
            from sqlalchemy import text
            from apps.api.main import _render_safe_database_url
            from packages.pms.database import online_engine

            raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
            if not raw:
                raise ValueError("missing URL")
            engine = online_engine(_render_safe_database_url(raw))
            with engine.connect() as conn, conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                db = database_checks(conn)
        except Exception as exc:
            db = [
                check(
                    "DATABASE_INSPECTION",
                    "BLOCKED",
                    {
                        "error_type": type(exc).__name__,
                        "note": "Connection/schema check unavailable; credentials and error messages withheld",
                    },
                )
            ]
        finally:
            if engine is not None:
                engine.dispose()
    report = assess(
        ROOT,
        load_strategy_config(),
        os.environ if a.check else {},
        plan=a.plan,
        db_checks=db,
    )
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    try:
        report["code_revision"] = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
        report["working_tree_dirty"] = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        )
    except (OSError, subprocess.SubprocessError):
        report["code_revision"] = "UNAVAILABLE"
        report["working_tree_dirty"] = None
    rendered = json.dumps(report, indent=2, allow_nan=False)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    return 0 if a.plan else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "Deployment assessment unavailable ("
            + type(exc).__name__
            + "); details withheld",
            file=sys.stderr,
        )
        raise SystemExit(2)
