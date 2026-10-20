from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.data_layer.cloud_sources import SourceError
from packages.data_layer.price_observations import (
    parse_security_report, reviewed_suspensions, verify_absences, SUSPENSION_DOCS,
)
from packages.data_layer.cloud_storage import CloudStore
from tests.test_cloud_collection import Engine, document, csv_body, price_zip, parse_bhavcopy, DAY


def report(**changes):
    r = dict(SYMBOL='TEST', SERIES='EQ', DATE1='16-Oct-2026', OPEN_PRICE='100',
             HIGH_PRICE='102', LOW_PRICE='99', CLOSE_PRICE='101', TTL_TRD_QNTY='1000')
    r.update(changes)
    return csv_body([r])


def test_secondary_report_rejects_wrong_day_and_duplicate_identity():
    with pytest.raises(SourceError, match='wrong_session'):
        parse_security_report(report(DATE1='15-Oct-2026'), DAY)
    body = report()
    with pytest.raises(SourceError, match='ambiguous'):
        parse_security_report(body + body.split(b'\r\n')[1] + b'\r\n', DAY)


def test_missing_bar_is_accounted_for_without_inserting_a_price():
    engine = Engine()
    store = CloudStore(engine)
    row = parse_bhavcopy(price_zip(), DAY)[0]
    # Another real security makes the primary and secondary reports nonempty.
    row['symbol'] = 'OTHER'
    row['isin'] = 'INE000000002'
    secondary = parse_security_report(report(SYMBOL='OTHER'), DAY)
    out = store.prices([row], document('primary'), DAY, {'TEST': {'status':'A'}},
                       secondary=secondary, secondary_doc=document('secondary'))
    assert out['complete'] and out['no_reported_bar_symbols'] == ['TEST']
    assert out['written'] == 0
    assert not any('insert into pms.daily_prices' in sql for sql, params in engine.calls)


def test_reported_missing_price_cannot_be_excused_by_negative_evidence():
    row = parse_bhavcopy(price_zip(), DAY)[0]
    secondary = parse_security_report(report(), DAY)
    with pytest.raises(SourceError, match='conflicts'):
        verify_absences([row], secondary, {'TEST'}, {})
    secondary[('TEST','EQ')]['volume'] = Decimal(999)
    with pytest.raises(SourceError, match='disagree'):
        verify_absences([row], secondary, set(), {})
    with pytest.raises(SourceError, match='disagree'):
        verify_absences([row], {('OTHER','EQ'):secondary[('TEST','EQ')]}, set(), {})


def test_reviewed_suspension_is_bounded_hashed_and_identity_checked():
    class HTTP:
        def fetch(self, url):
            key = url.rsplit('/',1)[-1][:-4]
            return SimpleNamespace(sha256=SUSPENSION_DOCS[key])
    known = {'CCAVENUE': {'isin':'INE483S01038'}}
    states, docs = reviewed_suspensions(HTTP(), date(2026,10,13), known)
    assert states['CCAVENUE']['status'] == 'S' and len(docs) == 2
    assert reviewed_suspensions(None, date(2026,10,19), known) == ({}, [])
    assert reviewed_suspensions(None, date(2026,10,12), known) == ({}, [])
    with pytest.raises(SourceError, match='identity'):
        reviewed_suspensions(HTTP(), DAY, {})
    http = SimpleNamespace(fetch=lambda url:SimpleNamespace(sha256='0'*64))
    with pytest.raises(SourceError, match='source_changed'):
        reviewed_suspensions(http, DAY, known)


def test_suspension_accounts_for_missing_status_without_inventing_asm_gsm():
    engine = Engine()
    store = CloudStore(engine)
    row = parse_bhavcopy(price_zip(), DAY)[0]
    row['symbol'] = 'OTHER'
    row['isin'] = 'INE000000002'
    engine.universe.append(dict(engine.universe[0], id=2, symbol='OTHER', isin=row['isin']))
    out = store.prices([row], document('primary'), DAY, {'OTHER': {'status':'A'}},
        secondary=parse_security_report(report(SYMBOL='OTHER'), DAY),
        secondary_doc=document('secondary'), suspensions={'TEST': {'status':'S'}},
        suspension_docs=[document('reviewed circular')])
    assert out['complete'] and out['uncovered_symbols'] == []
    assert not any('surveillance_history' in sql for sql, params in engine.calls)
