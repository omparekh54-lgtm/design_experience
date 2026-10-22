import copy
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from types import SimpleNamespace

import pytest
from packages.data_layer import dated_identity_repair as repair
from packages.data_layer.cloud_sources import Document, IST, SourceError
from packages.data_layer.cloud_storage import reviewed_price_isin_matches


def document(body):
    body = body if isinstance(body, bytes) else json.dumps(body).encode()
    return Document('https://www.nseindia.com/api/test', body,
        hashlib.sha256(body).hexdigest(), datetime(2026,10,22,10,tzinfo=IST),
        len(body), 'raw-sources/verified')


def sources(monkeypatch, change=None):
    notice = document(b'reviewed notice')
    monkeypatch.setattr(repair, 'NOTICE_SHA', notice.sha256)
    master = dict(symbol=repair.SYMBOL, isin=repair.NEW, name='JSW Dulux Limited', series='EQ')
    quote = dict(symbol=repair.SYMBOL, isinCode=repair.NEW, companyName=master['name'], series='EQ', marketType='N')
    if change == 'master': master['isin'] = repair.OLD
    if change == 'quote': quote['isinCode'] = repair.OLD
    if change == 'series': quote['series'] = 'BE'
    if change == 'company': quote['companyName'] = 'Other company'
    if change == 'notice': notice = document(b'changed notice')
    if change == 'future': notice = Document(notice.url,notice.body,notice.sha256,
        datetime(2026,10,21,10,tzinfo=IST),notice.size,notice.archive_key)
    monkeypatch.setattr(repair, 'parse_master', lambda body:{repair.SYMBOL:master})
    docs = [notice, document(b'master'), document({'equityResponse':[{'metaData':quote}]})]
    if change == 'unarchived':
        d = docs[1]; docs[1] = Document(d.url,d.body,d.sha256,d.observed_at,d.size)
    class HTTP:
        def fetch(self, url):return docs[0] if url==repair.NOTICE_URL else docs[1]
        def api(self, *args, **kwargs):return docs[2]
    return HTTP()


@pytest.mark.parametrize('change',['master','quote','series','company','notice','future','unarchived'])
def test_exact_dated_archived_proofs_are_required(monkeypatch,change):
    with pytest.raises(SourceError):repair.transition_evidence(sources(monkeypatch,change))


def test_transition_preserves_original_mapping_and_is_idempotent(monkeypatch):
    http = sources(monkeypatch)
    original = dict(id=2006,isin=repair.OLD,metadata={'external_mapping':'retained'})
    row = copy.deepcopy(original); writes=[]; receipts=[]
    class Result:
        def __init__(self,value):self.value=value
        def mappings(self):return self
        def one(self):return self.value
        def all(self):return self.value
    class Conn:
        def execute(self, sql, params=None):
            if 'safety_state' in str(sql):return Result(dict(execution_mode='PAPER',new_orders_enabled=False))
            return Result([row])
    @contextmanager
    def begin():yield Conn()
    def execute(c, sql, params):
        writes.append(str(sql));row['isin']=params['isin'];row['metadata'].update(json.loads(params['metadata']))
    store=SimpleNamespace(engine=SimpleNamespace(begin=begin),execute=execute,
        receipt=lambda *a:receipts.append(a),_known={})
    first=repair.reconcile_dated_identity(store,http)
    second=repair.reconcile_dated_identity(store,http)
    assert first['changed'] is True and second['changed'] is False
    assert len(writes)==1 and 'update pms.instruments' in writes[0]
    assert row['id']==original['id'] and row['metadata']['external_mapping']=='retained'
    assert all('financial' not in sql and 'daily_prices' not in sql for sql in writes)
    assert store._known is None and len(receipts)==2
    assert reviewed_price_isin_matches(row,repair.OLD,date(2026,10,21))
    assert not reviewed_price_isin_matches(row,repair.OLD,date(2026,10,22))
    assert not reviewed_price_isin_matches(row,repair.NEW,date(2026,10,21))
    assert reviewed_price_isin_matches(row,repair.NEW,date(2026,10,22))
    assert not reviewed_price_isin_matches(row,'INE000000000',date(2026,10,21))
    record=dict(isin=repair.OLD,date='30-Sep-2026')
    assert repair.shareholding_expected_isin(row,record)==repair.OLD
    assert repair.shareholding_expected_isin(row,{**record,'date':'22-Oct-2026'})==repair.NEW
    assert repair.shareholding_expected_isin(row,{**record,'isin':'INE000000000'})==repair.NEW
    assert record==dict(isin=repair.OLD,date='30-Sep-2026')
