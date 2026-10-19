"""Deferred, explicit restore rehearsal into a separate empty database only."""

import argparse, json, os, sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.pms.recovery import inspect_archive, restore_rehearsal


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Inspect a trusted backup or rehearse restore in an empty isolated database"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--restore-check", action="store_true")
    p.add_argument("--archive", type=Path)
    p.add_argument("--sha256")
    p.add_argument("--source-audit", type=Path)
    a = p.parse_args(argv)
    if a.plan:
        print(
            json.dumps(
                dict(
                    steps=[
                        "Produce a source integrity manifest at the backup snapshot",
                        "Create a trusted custom-format archive including external view dependencies",
                        "Verify archive SHA256 and table of contents",
                        "Restore into a separate empty scratch database",
                        "Compare the restored source-data manifest; review roles/grants and RPO/RTO separately",
                    ],
                    source_writes=False,
                    production_restore=False,
                ),
                indent=2,
            )
        )
        return 0
    if not a.archive or not a.sha256:
        p.error("--archive and --sha256 required")
    if a.inspect:
        print(json.dumps(inspect_archive(a.archive, a.sha256), indent=2))
        return 0
    if not a.source_audit:
        p.error("--restore-check requires --source-audit")
    from dotenv import load_dotenv
    from packages.pms.data_integrity import read_report, AuditScope, audit_manifest
    from packages.pms.matrix_data import consistent_inputs
    from packages.pms.database import online_engine
    from sqlalchemy.engine import make_url

    load_dotenv()
    source = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    target = os.getenv("PMS_RESTORE_TEST_URL")
    if not source or not target:
        p.error(
            "Source URL and PMS_RESTORE_TEST_URL required; never use the source as target"
        )
    evidence = read_report(a.source_audit)
    if not evidence.get("finished") or not evidence.get("dataset"):
        raise ValueError("Finished fingerprinted source audit required")
    scope = AuditScope(
        date.fromisoformat(evidence["requested_scope"]["start"]),
        date.fromisoformat(evidence["requested_scope"]["end"]),
    )
    engine = online_engine(target, worker=True)

    def verify(db):
        with consistent_inputs(db) as snapshot:
            manifest = audit_manifest(
                snapshot,
                scope,
                evidence.get("requested_symbols"),
                datetime.fromisoformat(evidence["created_at"]),
            )
        return manifest["fingerprint"] == evidence["dataset"]["fingerprint"]

    try:
        # pg_restore must enforce the same remote transport requirement as the connection probe.
        parsed = make_url(target)
        if (
            parsed.host not in {"localhost", "127.0.0.1", "::1", None}
            and "sslmode" not in parsed.query
        ):
            target = parsed.update_query_dict({"sslmode": "require"}).render_as_string(
                hide_password=False
            )
        report = restore_rehearsal(
            a.archive, a.sha256, source, target, engine, verify=verify
        )
        print(json.dumps(report, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "Backup verification stopped ("
            + type(exc).__name__
            + "); inspect the isolated environment. Credentials are not printed.",
            file=sys.stderr,
        )
        raise SystemExit(2)
