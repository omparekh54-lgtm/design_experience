"""One reviewed, dated exchange transition; never infer a general ISIN alias."""
from datetime import date, timedelta
import json

from sqlalchemy import text
from packages.data_layer.cloud_sources import SourceError, parse_master
from packages.data_layer.cloud_storage import encoded

SYMBOL = 'JSWDULUX'
OLD = 'INE133A01011'
NEW = 'INE133A01029'
EFFECTIVE = date(2026, 10, 22)
NOTICE_URL = 'https://nsearchives.nseindia.com/content/circulars/CML77001.pdf'
NOTICE_SHA = 'eb9b435bdd25be1d4615be761ce5d58f0a87438d2d709629f5f922e46ff3b588'


def transition_evidence(http):
    notice = http.fetch(NOTICE_URL)
    master = http.fetch('https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv')
    quote = http.api('NextApi/apiClient/GetQuoteApi', functionName='getSymbolData',
                     marketType='N', series='EQ', symbol=SYMBOL)
    current = parse_master(master.body).get(SYMBOL, {})
    rows = json.loads(quote.body).get('equityResponse') or []
    q = (rows[0].get('metaData') or {}) if len(rows) == 1 else {}
    if (notice.sha256 != NOTICE_SHA or notice.observed_at.date() < EFFECTIVE
            or current.get('isin') != NEW or current.get('name') != 'JSW Dulux Limited'
            or current.get('series') != 'EQ'
            or any(q.get(k) != v for k, v in dict(symbol=SYMBOL, isinCode=NEW,
                companyName='JSW Dulux Limited', series='EQ', marketType='N').items())):
        raise SourceError('dated_identity_evidence_mismatch')
    docs = [notice, master, quote]
    if any(not d.archive_key for d in docs):
        raise SourceError('dated_identity_archive_required')
    return docs, dict(previous_isin=OLD, current_isin=NEW, reviewed=True,
        current_effective_from=str(EFFECTIVE), allow_previous_through=str(EFFECTIVE-timedelta(days=1)),
        observed_on=str(master.observed_at.date()), master_sha256=master.sha256,
        quote_sha256=quote.sha256, notice_sha256=notice.sha256, notice_url=NOTICE_URL,
        evidence=[dict(url=d.url, sha256=d.sha256, archive_key=d.archive_key) for d in docs])


def reconcile_dated_identity(store, http):
    docs, review = transition_evidence(http)
    with store.engine.begin() as c:
        safety = c.execute(text('select execution_mode,new_orders_enabled from pms.safety_state where singleton=true for update')).mappings().one()
        if safety['execution_mode'] != 'PAPER' or safety['new_orders_enabled']:
            raise SourceError('repair_requires_paper_entries_disabled')
        rows = c.execute(text('''select id,isin,metadata from pms.instruments
            where exchange='NSE' and nse_symbol=:symbol and active for update'''),
            {'symbol': SYMBOL}).mappings().all()
        if len(rows) != 1 or rows[0]['isin'] not in {OLD, NEW}:
            raise SourceError('dated_identity_database_mismatch')
        row = rows[0]
        metadata = row['metadata'] or {}
        history = metadata.get('reviewed_isin_history', [])
        if not isinstance(history, list):
            raise SourceError('dated_identity_history_invalid')
        existing = [r for r in history if r.get('previous_isin') == OLD and r.get('current_isin') == NEW]
        if existing and (len(existing) != 1 or any(existing[0].get(k) != review[k]
                for k in ('current_effective_from', 'allow_previous_through', 'notice_sha256', 'reviewed'))):
            raise SourceError('dated_identity_history_conflict')
        changed = row['isin'] == OLD or not existing
        if changed:
            if not existing:
                history = [*history, review]
            # Retain instrument ID/external mappings, source payloads, periods and timestamps.
            store.execute(c, '''update pms.instruments set isin=:isin,
                metadata=coalesce(metadata,'{}'::jsonb) || cast(:metadata as jsonb) where id=:id''',
                dict(id=row['id'], isin=NEW, metadata=encoded(dict(reviewed_isin_history=history,
                    face_value='1', paid_up_value='1'))))
        store.receipt(c, 'REVIEWED_DATED_IDENTITY', docs,
            dict(instrument_id=row['id'], symbol=SYMBOL, review=review, changed=changed))
    store._known = None
    return dict(status='SUCCESS', symbol=SYMBOL, previous_isin=OLD, current_isin=NEW,
                effective_from=str(EFFECTIVE), changed=changed)


def shareholding_expected_isin(instrument, record):
    """A prior-period filing must still prove its original period-bound identity."""
    from packages.data_layer.cloud_sources import parse_day
    from packages.data_layer.cloud_storage import reviewed_price_isin_matches
    candidate = record.get('isin')
    if candidate and candidate != instrument['isin']:
        if reviewed_price_isin_matches(instrument, candidate, parse_day(record['date'])):
            return candidate
    return instrument['isin']
