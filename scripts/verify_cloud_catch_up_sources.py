"""Read-only full-backlog source verification before an initial cloud catch-up."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import event, text
from packages.data_layer.cloud_sources import (IST, OfficialHTTP, SourceError,
    rows_csv, json_rows, paged_financials, parse_shareholding, parse_stamp,
    fetch_classification, parse_master)
from packages.data_layer.cloud_storage import CloudStore
from packages.data_layer.index_membership import equity_membership
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, filing_from_exchange_record
from packages.data_layer.action_identity import validate_action_identities
from packages.pms.cloud_collection import parse_actions, parse_meetings, record_key, CloudCollector
from packages.pms.database import online_engine
from scripts.bootstrap_cloud_collection import validate_target, check_database
from scripts.copy_pms_to_neon import connection_url


PENDING_SQL = """select p.metadata->'record' record from pms.provider_snapshots p
where p.provider='NSE' and p.dataset=:pending and not exists(
select 1 from pms.provider_snapshots a where a.provider='NSE' and a.dataset=:ack
and a.metadata->>'complete'='true' and a.metadata->>'record_key'=p.metadata->>'record_key')
order by p.observed_at,p.id"""


def readonly_connections(engine):
    @event.listens_for(engine, 'connect')
    def pin_readonly(dbapi, _):
        with dbapi.cursor() as cursor:
            cursor.execute('SET default_transaction_read_only=on')
        dbapi.commit()


def merge_pending(pending, discovered):
    return list({record_key(r): r for r in [*pending, *discovered]}.values())


def classification_due(row, day):
    observed = (row.get('metadata') or {}).get('sector_classification', {}).get('observed_at')
    return (not row.get('sector') or not observed
            or datetime.fromisoformat(observed).date() < day - timedelta(days=60))


class SourceBudget:
    def __init__(self):
        self.deadline = time.monotonic() + 45 * 60
        self.bytes = 0
        self.lock = threading.Lock()

    def http(self):
        budget = self
        class BoundedHTTP(OfficialHTTP):
            def fetch(self, url):
                self.deadline = min(self.deadline, budget.deadline)
                doc = super().fetch(url)
                with budget.lock:
                    budget.bytes += doc.size
                    if budget.bytes > 1024 * 1024 * 1024:
                        raise SourceError('preflight_download_budget_exceeded')
                return doc
        return BoundedHTTP()


def verify_item(item, known, budget, now):
    kind, row = item
    symbol = row['symbol']
    try:
        http = budget.http()
        if kind == 'classification':
            doc, _ = fetch_classification(http, symbol, known[symbol]['isin'])
        elif kind == 'shareholding':
            if parse_stamp(row['broadcastDate']) > now:
                raise SourceError('shareholding_filing_in_future')
            doc = http.fetch(row['xbrl'])
            parse_shareholding(doc.body, row, expected_isin=known[symbol]['isin'])
        else:
            parser = object.__new__(NSEIntegratedFinancialsProvider)
            record = parser._parse_row(row)
            if record is None:
                raise SourceError('persisted_financial_record_invalid')
            from packages.pms.module_a_context import as_ist
            if as_ist(record.filed_at) > now:
                raise SourceError('financial_filing_in_future')
            doc = http.fetch(record.xbrl_uri)
            if b'<!DOCTYPE' in doc.body.upper() or b'<!ENTITY' in doc.body.upper():
                raise SourceError('xbrl_entities_forbidden')
            filing = filing_from_exchange_record(record, doc.body)
            if filing.sales is None or filing.pat is None:
                raise SourceError('financial_core_facts_unavailable')
        return {'kind': kind, 'symbol': symbol, 'code': 'OK', 'sha256': doc.sha256}
    except Exception as exc:
        return {'kind': kind, 'symbol': symbol,
                'code': str(exc) if isinstance(exc, SourceError) else 'preflight_parser_unavailable',
                'error_type': type(exc).__name__}


def verify(engine, target, *, now=None, workers=8):
    now = now or datetime.now(IST)
    validate_target(target, now, 24)
    if not 1 <= workers <= 8:
        raise SourceError('preflight_worker_budget_invalid')
    check_database(engine)
    store = CloudStore(engine)
    known = store.instruments()
    budget = SourceBudget()
    http = budget.http()
    report = {'status': 'VERIFYING', 'database_writes': False, 'campaign_started': False,
              'paper_enabled': False, 'trade_date': str(target), 'checks': []}
    with engine.connect() as c:
        if c.execute(text('SHOW transaction_read_only')).scalar_one() != 'on':
            raise SourceError('preflight_read_only_required')
        pending = {kind: [r['record'] for r in c.execute(text(PENDING_SQL),
            {'pending': f'CLOUD_{dataset}_PENDING', 'ack': f'CLOUD_{dataset}_XML'}).mappings()]
            for kind, dataset in [('financial', 'FINANCIAL'), ('shareholding', 'SHAREHOLDING')]}
    master_doc = http.fetch('https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv')
    master = parse_master(master_doc.body)
    if any(s not in known or known[s]['isin'] != row['isin'] for s, row in master.items()):
        raise SourceError('preflight_master_identity_unresolved')
    doc = http.fetch('https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv')
    members = rows_csv(doc.body, ['Company Name', 'Industry', 'Symbol', 'Series', 'ISIN Code'])
    if len(members) < 490:
        raise SourceError('index_snapshot_incomplete')
    included, excluded = equity_membership(members, known)
    report['membership'] = {'source_rows': len(members), 'equity_members': len(included),
                            'excluded_from_equity_universe': excluded}
    financials = paged_financials(http, store.financial_window(now.date()), now.date())
    live_shp = json_rows(http.api('corporate-share-holdings-master', index='equities'))
    tasks = []
    for kind, discovered in [('financial', [r.raw_payload for r in financials]),
                              ('shareholding', live_shp)]:
        acked = store.acked(f'CLOUD_{"FINANCIAL" if kind == "financial" else "SHAREHOLDING"}_XML')
        rows = merge_pending(pending[kind], [r for r in discovered if r.get('symbol') in known])
        rows = [r for r in rows if record_key(r) not in acked]
        tasks.extend((kind, r) for r in rows)
    tasks.extend(('classification', {'symbol': s}) for s, r in known.items()
                 if classification_due(r, now.date()))
    if len(tasks) > 9000:
        raise SourceError('preflight_record_budget_exceeded')
    report['required'] = dict(Counter(kind for kind, _ in tasks))
    start, end = (now.date()-timedelta(days=7)).strftime('%d-%m-%Y'), (now.date()+timedelta(days=45)).strftime('%d-%m-%Y')
    actions = parse_actions(http.api('corporates-corporateActions', index='equities', from_date=start, to_date=end), now)
    validate_action_identities(actions, known)
    parse_meetings(http.api('corporate-board-meetings', index='equities', from_date=start, to_date=end), now)
    core = CloudCollector(store, http, now=now, catch_up_date=target).core(target, apply=False)
    if core['status'] != 'SUCCESS':
        raise SourceError('preflight_completed_session_required')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(verify_item, task, known, budget, now) for task in tasks]
        for future in as_completed(futures):
            result = future.result()
            report['checks'].append(result)
            if result['code'] != 'OK':
                print(json.dumps({'source_exception': result}), flush=True)
            if len(report['checks']) % 100 == 0:
                print(json.dumps({'checked': len(report['checks']), 'required': len(tasks),
                                  'codes': dict(Counter(r['code'] for r in report['checks']))}), flush=True)
    errors = [r for r in report['checks'] if r['code'] != 'OK']
    report.update(status='BLOCKED' if errors else 'SOURCE_CHECKS_PASSED',
                  errors=errors, downloaded_bytes=budget.bytes, finished_at=datetime.now(IST).isoformat(),
                  limitations=['Read-only source verification; not a completed data import or PAPER permission'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Preserve existing evidence; choose a new output')
    engine = None
    try:
        engine = online_engine(connection_url(os.environ.get('DATABASE_URL', ''), destination=True), worker=True)
        readonly_connections(engine)
        report = verify(engine, args.date)
    except Exception as exc:
        report = {'status': 'BLOCKED', 'database_writes': False, 'campaign_started': False,
                  'error_code': str(exc) if isinstance(exc, SourceError) else 'preflight_unavailable',
                  'error_type': type(exc).__name__}
    finally:
        if engine is not None:
            engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'checks', 'errors'}}, default=str), flush=True)
    return 0 if report['status'] == 'SOURCE_CHECKS_PASSED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
