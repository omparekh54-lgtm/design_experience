from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal
import hashlib
import pytest
from packages.data_layer.cloud_sources import Document,IST,SourceError
from packages.data_layer.exchange_financials import ExchangeFilingRecord,filings_from_exchange_record
from packages.data_layer.nse_financial_render import financial_document
from tests.test_exchange_financials import SAMPLE_XBRL

URI='https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_123_21102026171220_iXBRL_WEB.html'


def record():
    return ExchangeFilingRecord(exchange='NSE',symbol='TEST',company_name='Test Limited',
        period_end=date(2026,9,30),filed_at=datetime(2026,10,21,17,tzinfo=IST),
        statement_scope='standalone',submission_type='Original',filing_kind='quarter',
        xbrl_uri='https://nsearchives.nseindia.com/corporate/xbrl/original.xml',
        raw_payload={'seq_Id':'123','ixbrl':URI,'original':'retained'})


def html():
    header=[('NSE Symbol','TEST'),('ISIN','INE000A01010'),('Name of company','Test Limited'),
        ('Nature of report standalone or consolidated','Standalone'),
        ('Description of presentation currency','INR'),('Level of rounding used in financial results','Lakhs')]
    rows=[('Date of start of reporting period','01-07-2026','01-04-2026'),
        ('Date of end of reporting period','30-09-2026','30-09-2026'),
        ('Nature of report standalone or consolidated','Standalone','Standalone'),
        ('Revenue from operations','12,000','22,000'),('Total profit (loss) for period','(300)','900'),
        ('Diluted earnings (loss) per share from continuing and discontinued operations','0.20','0.60')]
    return ('<!DOCTYPE html><html><table>'+''.join('<tr><td>'+k+'</td><td>'+v+'</td></tr>' for k,v in header)+
        '</table><table>'+''.join('<tr><td></td><td>'+k+'</td><td>'+v+'</td><td>'+y+'</td></tr>' for k,v,y in rows)+
        '</table></html>').encode()


def fetched(body, r=None):
    original=r or record()
    class HTTP:
        def fetch(self,uri,**kwargs):
            if uri==original.xbrl_uri:raise SourceError('source_http_404')
            assert kwargs=={'financial_render':True} and uri==URI
            return Document(uri,body,hashlib.sha256(body).hexdigest(),datetime.now(IST),len(body),'raw-sources/test')
    return financial_document(HTTP(),original,expected_isin='INE000A01010')


def test_render_preserves_exact_primary_period_scope_timestamp_and_units():
    original=record();r,doc=fetched(html(),original)
    f=filings_from_exchange_record(r,doc.body)[0]
    assert f.sales==Decimal('120') and f.pat==Decimal('-3') and f.diluted_eps==Decimal('0.20')
    assert f.filing_kind=='quarter' and f.period_end==original.period_end and f.filed_at==original.filed_at
    assert f.statement_scope=='standalone' and f.xbrl_uri==original.xbrl_uri
    assert f.raw_payload['financial_extraction']['version']=='FINANCIAL_XBRL_v2'
    assert f.raw_payload['financial_source']['archive_key']=='raw-sources/test'
    assert f.operating_cash_flow is None and original.raw_payload=={'seq_Id':'123','ixbrl':URI,'original':'retained'}


@pytest.mark.parametrize('old,new',[(b'TEST',b'OTHER'),(b'INE000A01010',b'INE000A01028'),
    (b'Test Limited',b'Other Limited'),(b'Lakhs',b'Unknown'),(b'INR',b'USD'),
    (b'30-09-2026',b'30-06-2026'),(b'Standalone',b'Consolidated'),(b'12,000',b'NaN')])
def test_render_fails_closed_on_ambiguous_identity_period_scope_or_units(old,new):
    r,d=fetched(html().replace(old,new))
    with pytest.raises(ValueError):filings_from_exchange_record(r,d.body)


def test_no_fallback_to_unpaired_document():
    r=replace(record(),raw_payload={**record().raw_payload,'seq_Id':'456'})
    with pytest.raises(SourceError,match='pair_unavailable'):fetched(html(),r)


def test_half_year_is_not_a_synthetic_quarter():
    # A declared six-month source retains its own duration; no division/YTD subtraction.
    body=SAMPLE_XBRL.replace(b'<in-gaap:RevenueFromOperations contextRef="YTD"',
        b'<in-gaap:ReportingQuarter contextRef="YTD">Half yearly</in-gaap:ReportingQuarter><in-gaap:RevenueFromOperations contextRef="YTD"')
    body=body.replace(b'contextRef="Q1"',b'contextRef="YTD"')
    r=replace(record(),period_end=date(2026,6,30),raw_payload={})
    # Contradictory YTD sales remain unavailable rather than choosing one value.
    f=filings_from_exchange_record(r,body)[0]
    assert f.filing_kind=='half_year' and f.sales is None
    assert f.pat==Decimal('15')
