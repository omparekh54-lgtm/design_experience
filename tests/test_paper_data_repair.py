from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
import hashlib
import pytest
from packages.data_layer.cloud_sources import Document, IST, SourceError
from packages.pms.paper_data_repair import archived_document, verify_reparse, safe_error, PaperDataRepair
from test_exchange_financials import SAMPLE_XBRL


def row():
    return dict(id=42,source='NSE',symbol='ABC',period_end=date(2026,6,30),
                filing_timestamp=datetime(2026,8,12,19,26,tzinfo=IST),consolidated=True,
                filing_kind='quarter',revision_number=3,raw_payload={'original':'retained'},
                xbrl_uri='https://nsearchives.nseindia.com/abc.xml')


def test_reparse_preserves_identity_and_original_revision():
    r=row();sha=hashlib.sha256(SAMPLE_XBRL).hexdigest()
    doc=Document(r['xbrl_uri'],SAMPLE_XBRL,sha,datetime.now(IST),len(SAMPLE_XBRL),'raw-sources/abc')
    f=verify_reparse(r,doc)[0]
    assert f.version==3
    assert f.filed_at==r['filing_timestamp'] and f.period_end==r['period_end']
    assert f.raw_payload['original']=='retained'
    assert f.raw_payload['financial_extraction']['version']=='FINANCIAL_XBRL_v2'
    assert f.raw_payload['source_reprocessing']['sha256']==sha
    assert f.sales==120


def test_archive_read_requires_matching_checksum_and_closes_stream():
    stream=BytesIO(b'wrong')
    archive=SimpleNamespace(client=SimpleNamespace(get_object=lambda **kw: {'Body':stream}))
    r={**row(),'archive_key':'raw-sources/abc','archived_sha':'0'*64}
    with pytest.raises(SourceError,match='checksum_mismatch'):archived_document(archive,r)
    assert stream.closed


def test_reparse_rejects_xml_entities():
    r=row();body=b'<!DOCTYPE x [<!ENTITY e "abc">]><x/>'
    with pytest.raises(SourceError,match='entities_forbidden'):
        verify_reparse(r,Document(r['xbrl_uri'],body,'0'*64,datetime.now(IST),len(body)))


def test_source_errors_do_not_expose_credentials():
    assert safe_error(SourceError('source_failed'))=='source_failed'
    assert safe_error(SourceError('https://secret:password@host'))=='SourceError'
    assert safe_error(RuntimeError('secret'))=='RuntimeError'


def test_repair_refuses_enabled_entries():
    result=SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:dict(execution_mode='PAPER',new_orders_enabled=True)))
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):return result
    service=PaperDataRepair(SimpleNamespace(connect=Connection),None,None)
    with pytest.raises(SourceError,match='entries_disabled'):service.locked()


def test_sector_repair_prioritizes_missing_dated_coverage(monkeypatch):
    from packages.data_layer.cloud_storage import CloudStore
    import packages.data_layer.cloud_sources as sources
    called=[]
    class Result:
        def scalars(self):return [1]
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args,**kwargs):return Result()
    engine=SimpleNamespace(connect=Conn,begin=Conn)
    store=CloudStore(engine)
    store.instruments=lambda: {s:dict(id=i,isin='ISIN',sector='Services',metadata={}) for i,s in [(1,'COVERED'),(2,'GAP')]}
    store.execute=lambda *args:None
    def fetch(*args):
        called.append(args[1]);raise SourceError('source_unavailable')
    monkeypatch.setattr(sources,'fetch_classification',fetch)
    store.classifications(None,date(2026,10,22),limit=1)
    assert called==['GAP']


def test_sector_job_finishes_overdue_provenance_even_after_dated_gaps_are_filled(monkeypatch):
    import packages.pms.paper_data_repair as repair
    service=PaperDataRepair(None,None,None)
    service.locked=lambda:None
    service.counts=lambda:dict(sector_pending=0)
    results=iter([dict(due=2,remaining=1),dict(due=1,remaining=0)])
    class Store:
        def __init__(self,*args):pass
        def classifications(self,*args,**kwargs):return next(results)
    monkeypatch.setattr(repair,'CloudStore',Store)
    report=service.sectors(max_passes=3)
    assert report['status']=='SUCCESS' and len(report['passes'])==2
