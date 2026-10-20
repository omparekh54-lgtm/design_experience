"""Explicit, bounded initial catch-up on Neon; never runs the PAPER lifecycle."""
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
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.cloud_collection import run_collection
from packages.pms.database import online_engine
from scripts.copy_pms_to_neon import connection_url, LIMIT


def validate_target(target, now, passes):
    if not 1 <= (now.date() - target).days <= 7:
        raise SourceError('past_completed_session_required')
    if not 1 <= passes <= 6:
        raise SourceError('catch_up_pass_limit_invalid')


def check_database(engine):
    with engine.connect() as c, c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        state = dict(c.execute(text(
            'SELECT execution_mode,new_orders_enabled FROM pms.safety_state')).mappings().one())
        if state != {'execution_mode': 'PAPER', 'new_orders_enabled': False}:
            raise SourceError('paper_safety_not_locked')
        size = c.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
        if size >= LIMIT:
            raise SourceError('database_capacity_reserve_exceeded')
        return size


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date', type=date.fromisoformat, required=True)
    p.add_argument('--passes', type=int, default=6)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args(argv)
    now = datetime.now(IST)
    engine = None
    report = dict(status='BLOCKED', campaign_started=False, paper_enabled=False,
                  trade_date=str(a.date), passes=[], source_preserved=True)
    try:
        validate_target(a.date, now, a.passes)
        # Refuse arbitrary destinations and enforce verified TLS before any write.
        engine = online_engine(connection_url(os.environ.get('DATABASE_URL', ''),
                                             destination=True), worker=True)
        a.output_dir.mkdir(parents=True, exist_ok=False)
        for i in range(a.passes):
            before = check_database(engine)
            archive = PrivateArchive.from_env()
            result = run_collection(engine, now=datetime.now(IST), catch_up_date=a.date,
                max_filings=500, max_classifications=500,
                http=OfficialHTTP(archive=archive), require_archive=True)
            result['database_bytes_before'] = before
            result['database_bytes_after'] = check_database(engine)
            (a.output_dir / f'pass-{i+1}.json').write_text(
                json.dumps(result, default=str, indent=2) + '\n')
            report['passes'].append(dict(pass_number=i+1, status=result['status'],
                stages=result.get('stages', {}), database_bytes=result['database_bytes_after'],
                run_id=result.get('run_id')))
            report['status'] = result['status']
            print(json.dumps(report['passes'][-1], default=str), flush=True)
            if result['status'] != 'PARTIAL':
                break
        # SKIPPED, BUSY and partial data are never a completed deployment catch-up.
        report['full_universe_verified'] = report['status'] == 'SUCCESS'
        code = 0 if report['full_universe_verified'] else 2
    except Exception as exc:
        report.update(status='BLOCKED', full_universe_verified=False,
                      error_code=str(exc) if isinstance(exc, SourceError)
                      else 'initial_catch_up_unavailable', error_type=type(exc).__name__)
        code = 2
    finally:
        if engine is not None:
            engine.dispose()
    # Fixed, credential-free errors only. Preserve pass receipts on failure.
    print(json.dumps(report, default=str, indent=2), flush=True)
    if a.output_dir.is_dir():
        summary = a.output_dir / 'summary.json'
        if not summary.exists():
            summary.write_text(json.dumps(report, default=str, indent=2) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
