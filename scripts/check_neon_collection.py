"""Hosted, read-only database and private archive checks; never activates trading."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from packages.data_layer.cloud_sources import IST, OfficialHTTP, SourceError
from packages.data_layer.cloud_storage import CloudStore
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.cloud_collection import CloudCollector
from packages.pms.connection_config import connection_failure_code
from packages.pms.database import online_engine
from scripts.copy_pms_to_neon import CopyBlocked, LIMIT, connection_url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-date', type=date.fromisoformat)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output exists; preserve prior evidence')
    if args.probe_date and args.probe_date >= datetime.now(IST).date():
        parser.error('Probe date must be a past completed session')
    report = {'status': 'CHECKING', 'database_writes': False, 'campaign_started': False,
              'full_universe_verified': False, 'cutover': False, 'source_probe': 'NOT_RUN'}
    engine = None
    try:
        report['stage'] = 'database_configuration'
        raw = os.getenv('NEON_PMS_DATABASE_URL') or os.getenv('DATABASE_URL', '')
        engine = online_engine(connection_url(raw, destination=True), worker=True)
        report['stage'] = 'database_checks'
        with engine.connect() as c, c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            state = dict(c.execute(text(
                'SELECT execution_mode,new_orders_enabled FROM pms.safety_state')).mappings().one())
            if state != {'execution_mode': 'PAPER', 'new_orders_enabled': False}:
                raise SourceError('paper_safety_not_locked')
            report['database_bytes'] = c.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
            if report['database_bytes'] >= LIMIT:
                raise SourceError('database_capacity_reserve_exceeded')
            report['safety'] = state
        report['tls_verified_full'] = True
        report['writable_targets'] = CloudStore(engine).resolve_tables()
        report['stage'] = 'archive_checks'
        archive = PrivateArchive.from_env()
        report['raw_archive'] = archive.summary()
        if args.probe_date:
            report['stage'] = 'official_source_probe'
            collector = CloudCollector(http=OfficialHTTP(archive=archive), now=datetime.now(IST))
            try:
                report['source_probe'] = collector.collect(args.probe_date)
            finally:
                report['raw_archive'] = archive.summary()
                report['documents'] = [dict(url=d.url,sha256=d.sha256,bytes=d.size,
                    archive_key=d.archive_key) for d in collector.http.documents]
            if report['source_probe']['status'] != 'SUCCESS':
                raise SourceError('completed_source_session_not_verified')
        report.update(status='INFRA_AND_SOURCE_PROBE_VERIFIED' if args.probe_date else 'INFRA_VERIFIED',
                      stage='complete')
        code = 0
    except Exception as exc:
        report.update(status='BLOCKED', error_code=str(exc) if isinstance(exc, (SourceError, CopyBlocked))
                      else connection_failure_code(exc))
        code = 2
    finally:
        if engine is not None:
            engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, default=str, indent=2)
    args.output.write_text(rendered + '\n')
    print(rendered)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
