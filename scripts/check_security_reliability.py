"""Read-only deployment evidence. Deferred until the consolidated local setup."""

import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.pms.reliability import runtime_posture, ACCESS_SQL, reliability_report


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Read-only security/reliability posture; never unlocks execution"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--check", action="store_true")
    a = p.parse_args(argv)
    if a.plan:
        print(
            json.dumps(
                dict(
                    checks=[
                        "mutation authentication",
                        "database availability and grants",
                        "worker failures/stale journals",
                        "TLS/private broker transport",
                        "independent backup restore evidence",
                    ],
                    changes_database=False,
                    execution_readiness=False,
                ),
                indent=2,
            )
        )
        return 0
    from dotenv import load_dotenv
    from sqlalchemy import text
    from apps.api.main import _render_safe_database_url
    from packages.pms.database import online_engine

    load_dotenv()
    url = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not url:
        p.error("DATABASE_URL or PMS_DATABASE_URL required after setup")
    engine = online_engine(_render_safe_database_url(url))
    try:
        report = reliability_report(engine)
        with engine.connect() as conn:
            conn.execute(text("select 1")).scalar_one()
            grants = [dict(r) for r in conn.execute(text(ACCESS_SQL)).mappings().all()]
        report["database_access_review"] = grants
        report["backup_restore_evidence"] = "REVIEW_REQUIRED"
        print(json.dumps(report, default=str, indent=2))
        return (
            2
            if grants
            or not report["posture"]["operator_key_accepted"]
            or report["workers"]["status"] != "OBSERVED"
            else 0
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "Posture check unavailable ("
            + type(exc).__name__
            + "); credentials are not printed.",
            file=sys.stderr,
        )
        raise SystemExit(2)
