"""Financial duration/scope regressions and filing-to-Module-A contracts."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import permutations

import pytest

from packages.data_layer.exchange_financials import (
    BSEFinancialResultsProvider, NSEIntegratedFinancialsProvider,
    ExchangeFilingRecord, filing_from_exchange_record, filings_from_exchange_record,
    prefer_nse_then_bse, FINANCIAL_ACK_DATASET,
)
from packages.data_layer.storage import InMemoryStorage
from packages.data_layer.xbrl import extract_canonical_financials, parse_xbrl_facts
from packages.pms.fundamental_history import calculate_financial_history

END = date(2024, 3, 31)
RECORD = ExchangeFilingRecord('NSE', 'TEST', 'Test Ltd', END,
    datetime(2024, 5, 1, 16), 'consolidated', 'Original', raw_payload={'id': 'source-1'})


def xml_doc(facts, contexts=None):
    contexts = contexts or [context('Q', '2024-01-01'), context('Y', '2023-04-01')]
    return ('<xbrl xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:in="urn:indas" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + ''.join(contexts)
        + '<unit id="INR"><measure>iso4217:INR</measure></unit>'
        + '<unit id="EPS"><divide><unitNumerator><measure>iso4217:INR</measure>'
          '</unitNumerator><unitDenominator><measure>xbrli:shares</measure></unitDenominator></divide></unit>'
        + ''.join(facts) + '</xbrl>').encode()


def context(cid, start, dimension='', *, entity='TEST', end='2024-03-31'):
    period = f'<startDate>{start}</startDate><endDate>{end}</endDate>' if start else f'<instant>{end}</instant>'
    return f'<context id="{cid}"><entity><identifier>{entity}</identifier></entity><period>{period}</period>{dimension}</context>'


def fact(name, value, cid='Q', unit='INR'):
    return f'<in:{name} contextRef="{cid}" unitRef="{unit}">{value}</in:{name}>'


def test_year_end_disclosure_retains_quarter_and_year_with_original_timestamp():
    xml = xml_doc([fact('RevenueFromOperations', 30_0000000),
        fact('ProfitLossForPeriod', 10_0000000), fact('ProfitLossForPeriod', 100_0000000, 'Y'),
        fact('RevenueFromOperations', 300_0000000, 'Y'),
        fact('CashFlowsFromUsedInOperatingActivities', 80_0000000, 'Y')])
    filings = filings_from_exchange_record(RECORD, xml)
    q, y = filings
    assert (q.filing_kind, q.sales, q.pat) == ('quarter', 30, 10)
    assert (y.filing_kind, y.sales, y.pat, y.operating_cash_flow) == ('annual', 300, 100, 80)
    assert q.operating_cash_flow is None  # Never place annual CF in a quarter.
    assert all(f.filed_at == RECORD.filed_at and f.version == 1 for f in filings)
    assert RECORD.raw_payload == {'id': 'source-1'}
    assert y.raw_payload['financial_extraction']['fields']['pat']['contexts'] == ['Y']
    history = [dict(period_end=f.period_end, filed_at=f.filed_at, filing_kind=f.filing_kind,
        consolidated=True, facts={'sales': f.sales, 'pat': f.pat, 'operating_cash_flow': f.operating_cash_flow})
        for f in filings]
    assert calculate_financial_history(history).ocf_to_pat == pytest.approx(.8)
    assert [f.filing_kind for f in filings_from_exchange_record(replace(RECORD, filing_kind='annual'), xml)] == ['annual', 'quarter']


@pytest.mark.parametrize('provider', [NSEIntegratedFinancialsProvider, BSEFinancialResultsProvider])
@pytest.mark.parametrize('field,label', [('filing_kind','annual'),('periodType','Year Ended'),('resultType','Annual')])
def test_provider_identifies_explicit_annual_type(provider, field, label):
    p = provider.__new__(provider)
    row = dict(symbol='TEST', quarterEndDate='31-Mar-2024', broadcastDateTime='01-May-2024 16:00:00',
        consolidatedStandalone='Consolidated', **{field: label})
    r = p._parse_row(row) if provider is NSEIntegratedFinancialsProvider else p._parse_json_row(row, 'TEST')
    assert r.filing_kind == 'annual'


def test_audited_march_quarter_does_not_become_annual_without_year_contexts():
    p = NSEIntegratedFinancialsProvider.__new__(NSEIntegratedFinancialsProvider)
    r = p._parse_row(dict(symbol='TEST', quarterEndDate='31-Mar-2024',
        broadcastDateTime='01-May-2024 16:00:00', audited='Audited'))
    assert r.filing_kind == 'quarter'
    fs = filings_from_exchange_record(r, xml_doc([fact('ProfitLossForPeriod', 10_0000000)]))
    assert len(fs) == 1


def test_bse_annual_html_row_uses_the_same_duration_contract():
    p = BSEFinancialResultsProvider.__new__(BSEFinancialResultsProvider)
    r = p._parse_html('<table><tr><td>Consolidated-Mar-24 Annual</td><td>01-05-2024 16:00:00</td>'
        '<td><a href="a.xml">XBRL</a></td></tr></table>', 'TEST', p.BASE_URL)[0]
    assert r.filing_kind == 'annual'


def test_segment_fact_order_cannot_change_entity_totals():
    segment = '<scenario><xbrldi:explicitMember dimension="in:SegmentsAxis">in:ExportMember</xbrldi:explicitMember></scenario>'
    contexts = [context('Q', '2024-01-01'), context('SEG', '2024-01-01', segment)]
    facts = [fact('RevenueFromOperations', 100_0000000), fact('RevenueFromOperations', 10_0000000, 'SEG')]
    for order in permutations(facts):
        result = filing_from_exchange_record(RECORD, xml_doc(order, contexts))
        assert result.sales == 100
    assert filing_from_exchange_record(RECORD, xml_doc(facts[1:], contexts)).sales is None


@pytest.mark.parametrize('member,expected', [('ConsolidatedMember', 100), ('StandaloneMember', None)])
def test_scope_dimensions_match_requested_statement(member, expected):
    dim = f'<scenario><xbrldi:explicitMember dimension="in:ConsolidatedAndStandaloneAxis">in:{member}</xbrldi:explicitMember></scenario>'
    x = xml_doc([fact('RevenueFromOperations', 100_0000000)], [context('Q','2024-01-01', dim)])
    assert filing_from_exchange_record(RECORD, x).sales == expected


@pytest.mark.parametrize('extra', [
    '<scenario><xbrldi:typedMember dimension="in:SegmentAxis"><in:Segment>Retail</in:Segment></xbrldi:typedMember></scenario>',
    '<entity><segment><in:Segment>Retail</in:Segment></segment></entity>',
])
def test_unknown_context_content_is_not_an_entity_total(extra):
    x = xml_doc([fact('RevenueFromOperations', 100_0000000)], [context('Q','2024-01-01', extra)])
    assert filing_from_exchange_record(RECORD, x).sales is None


def test_conflicting_entity_facts_do_not_depend_on_xml_order():
    fs = [fact('ProfitLossForPeriod', 10_0000000), fact('ProfitLossForPeriod', 20_0000000)]
    for order in permutations(fs):
        f = filing_from_exchange_record(RECORD, xml_doc(order))
        assert f.pat is None
        assert f.raw_payload['financial_extraction']['fields']['pat']['status'] == 'ambiguous'
    assert filing_from_exchange_record(RECORD, xml_doc([fs[0], fs[0]])).pat == 10


def test_unknown_context_missing_duration_wrong_units_and_nonfinite_never_pass():
    for value in ['NaN', 'Infinity', '-Infinity']:
        assert filing_from_exchange_record(RECORD, xml_doc([fact('RevenueFromOperations', value)])).sales is None
    for cid, unit in [('MISSING','INR'), ('Q','EPS'), ('Q','UNKNOWN')]:
        assert filing_from_exchange_record(RECORD, xml_doc([fact('RevenueFromOperations', 100_0000000, cid, unit)])).sales is None
    # A six/nine-month YTD number is never substituted for a missing quarter.
    for start in ['2023-10-01', '2023-07-01']:
        assert filing_from_exchange_record(RECORD, xml_doc([fact('ProfitLossForPeriod', 90_0000000)], [context('Q',start)])).pat is None
    x = xml_doc([fact('ProfitLossForPeriod', 10_0000000)], [context('Q','2024-01-01', end='2024-03-30')])
    assert filing_from_exchange_record(RECORD, x).pat is None


def test_specific_operating_revenue_preferred_to_total_revenue():
    f = filing_from_exchange_record(RECORD, xml_doc([fact('TotalRevenue', 200_0000000), fact('RevenueFromOperations', 100_0000000)]))
    assert f.sales == 100


def test_negative_profit_zero_other_income_eps_and_balance_sheet_instant():
    x = xml_doc([fact('ProfitLossForPeriod', -10_0000000), fact('OtherIncome', 0),
        fact('DilutedEarningsLossPerShare', '2.5', unit='EPS'), fact('TotalBorrowings', 25_0000000, 'BS'),
        fact('TotalEquity', 100_0000000, 'BS')], [context('Q','2024-01-01'),context('BS',None)])
    f = filing_from_exchange_record(RECORD, x)
    assert (f.pat, f.other_income, f.diluted_eps, f.debt, f.equity) == (-10, 0, Decimal('2.5'), 25, 100)


def test_different_entities_cannot_be_collapsed_to_one_total():
    x = xml_doc([fact('ProfitLossForPeriod', 10_0000000), fact('ProfitLossForPeriod', 10_0000000, 'OTHER')],
        [context('Q','2024-01-01'), context('OTHER','2024-01-01', entity='OTHER')])
    assert filing_from_exchange_record(RECORD, x).pat is None


def test_nil_values_entities_and_duplicate_contexts_are_rejected():
    nil = '<in:ProfitLossForPeriod contextRef="Q" unitRef="INR" xsi:nil="true">100000000</in:ProfitLossForPeriod>'
    assert filing_from_exchange_record(RECORD, xml_doc([nil])).pat is None
    with pytest.raises(ValueError, match='duplicate_xbrl_context'):
        parse_xbrl_facts(xml_doc([], [context('Q','2024-01-01'),context('Q','2024-01-01')]))
    with pytest.raises(ValueError, match='xbrl_entities_forbidden'):
        parse_xbrl_facts(b'<!DOCTYPE x [<!ENTITY y "x">]><x/>')


def test_quarter_year_and_consolidated_standalone_are_distinct_storage_identities():
    q = filing_from_exchange_record(RECORD, xml_doc([fact('ProfitLossForPeriod', 10_0000000)]))
    rows = [q, replace(q, filing_kind='annual'), replace(q, statement_scope='standalone')]
    store = InMemoryStorage()
    assert store.append_filings(rows) == 3
    assert len(store.filings) == 3
    store.append_filings(rows)
    assert len(store.filings) == 3
    assert len(prefer_nse_then_bse(rows[:2], [])) == 2
    assert FINANCIAL_ACK_DATASET != 'CLOUD_FINANCIAL_XML'


def test_filing_publication_uses_latest_timestamp_in_india_time():
    p = NSEIntegratedFinancialsProvider.__new__(NSEIntegratedFinancialsProvider)
    r = p._parse_row(dict(symbol='TEST', quarterEndDate='31-Mar-2024',
        broadcastDateTime='2024-05-01T10:00:00Z',
        exchangeDisseminationTime='2024-05-01T15:30:01+05:30',
        revisedDateTime='2024-05-01T10:10:00Z'))
    assert r.filed_at == datetime(2024,5,1,15,40)
    assert r.exchange_disseminated_at == datetime(2024,5,1,15,30,1)


def test_non_march_financial_year_and_missing_annual_pat_are_not_fabricated():
    end = date(2024,12,31)
    x = xml_doc([fact('RevenueFromOperations',100_0000000),fact('ProfitLossForPeriod',10_0000000)],
        [context('Q','2024-01-01',end=str(end))])
    fs = filings_from_exchange_record(replace(RECORD,period_end=end,filing_kind='annual'),x)
    assert len(fs) == 1 and fs[0].pat == 10
    # Only a quarterly PAT cannot be borrowed by a partial annual CF observation.
    x = xml_doc([fact('ProfitLossForPeriod',10_0000000),
        fact('CashFlowsFromUsedInOperatingActivities',80_0000000,'Y')])
    fs = filings_from_exchange_record(RECORD,x)
    assert fs[1].filing_kind == 'annual' and fs[1].pat is None
    assert fs[1].operating_cash_flow == 80


def test_annual_only_xml_is_identified_when_exchange_summary_omits_kind():
    fs = filings_from_exchange_record(RECORD, xml_doc([
        fact('RevenueFromOperations',300_0000000,'Y'),
        fact('ProfitLossForPeriod',100_0000000,'Y'),
        fact('CashFlowsFromUsedInOperatingActivities',80_0000000,'Y')]))
    assert len(fs) == 1 and fs[0].filing_kind == 'annual'
    assert (fs[0].sales,fs[0].pat,fs[0].operating_cash_flow) == (300,100,80)
    assert fs[0].filed_at == RECORD.filed_at


def test_direct_nonfinite_fact_cannot_bypass_xml_validation():
    from packages.data_layer.xbrl import XbrlFact
    f = XbrlFact('ProfitLossForPeriod',Decimal('NaN'),'INR','Q',date(2024,1,1),END,None)
    assert extract_canonical_financials([f],END)['pat'] is None
