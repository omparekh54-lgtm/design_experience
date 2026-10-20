from datetime import date, datetime
from decimal import Decimal
from dataclasses import replace
import pytest

from packages.data_layer.exchange_financials import (
    BSEFinancialResultsProvider,
    NSEIntegratedFinancialsProvider,
    ExchangeFilingRecord,
    filing_from_exchange_record,
)
from packages.data_layer.xbrl import extract_canonical_financials, parse_xbrl_facts


SAMPLE_XBRL = b'''<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
             xmlns:in-gaap="http://example.com/in-gaap"
             xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <xbrli:context id="Q1">
    <xbrli:entity><xbrli:identifier scheme="x">ABC</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="YTD">
    <xbrli:entity><xbrli:identifier scheme="x">ABC</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
  <xbrli:unit id="INRPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
  <in-gaap:RevenueFromOperations contextRef="YTD" unitRef="INR">2000000000</in-gaap:RevenueFromOperations>
  <in-gaap:RevenueFromOperations contextRef="Q1" unitRef="INR">1200000000</in-gaap:RevenueFromOperations>
  <in-gaap:ProfitLossForPeriod contextRef="Q1" unitRef="INR">150000000</in-gaap:ProfitLossForPeriod>
  <in-gaap:ProfitLossBeforeTax contextRef="Q1" unitRef="INR">200000000</in-gaap:ProfitLossBeforeTax>
  <in-gaap:OtherIncome contextRef="Q1" unitRef="INR">10000000</in-gaap:OtherIncome>
  <in-gaap:DilutedEarningsLossPerShare contextRef="Q1" unitRef="INRPerShare">8.25</in-gaap:DilutedEarningsLossPerShare>
</xbrli:xbrl>
'''


def test_xbrl_parser_prefers_quarter_context_and_normalizes_inr_to_crore():
    facts = parse_xbrl_facts(SAMPLE_XBRL)
    result = extract_canonical_financials(facts, date(2026, 6, 30))
    assert result["sales"] == Decimal("120")
    assert result["pat"] == Decimal("15")
    assert result["pbt"] == Decimal("20")
    assert result["other_income"] == Decimal("1")
    assert result["diluted_eps"] == Decimal("8.25")


def test_nse_row_parser_preserves_point_in_time_provenance():
    provider = NSEIntegratedFinancialsProvider.__new__(NSEIntegratedFinancialsProvider)
    provider.BASE_URL = "https://www.nseindia.com"
    record = provider._parse_row(
        {
            "symbol": "ABC",
            "companyName": "ABC Limited",
            "quarterEndDate": "30-JUN-2026",
            "typeOfSubmission": "Revised",
            "auditedUnaudited": "Un-Audited",
            "consolidatedStandalone": "Consolidated",
            "broadcastDateTime": "12-Aug-2026 19:26:10",
            "revisedDateTime": "13-Aug-2026 09:10:00",
            "revisionRemarks": "EPS corrected",
            "exchangeReceivedTime": "12-Aug-2026 19:26:10",
            "exchangeDisseminationTime": "12-Aug-2026 19:26:11",
            "xbrlFile": "/xbrl/abc.xml",
        }
    )
    assert record is not None
    assert record.symbol == "ABC"
    assert record.period_end == date(2026, 6, 30)
    assert record.statement_scope == "consolidated"
    assert record.submission_type == "Revised"
    assert record.revision_remarks == "EPS corrected"
    assert record.xbrl_uri == "https://www.nseindia.com/xbrl/abc.xml"


def test_exchange_record_becomes_canonical_financial_filing():
    record = ExchangeFilingRecord(
        exchange="NSE",
        symbol="ABC",
        company_name="ABC Limited",
        period_end=date(2026, 6, 30),
        filed_at=datetime(2026, 8, 12, 19, 26, 10),
        statement_scope="consolidated",
        submission_type="Original",
        audit_status="Un-Audited",
        xbrl_uri="https://example.com/abc.xml",
    )
    filing = filing_from_exchange_record(record, SAMPLE_XBRL)
    assert filing.instrument_id == "NSE:ABC"
    assert filing.version == 1
    assert filing.sales == Decimal("120")
    assert filing.pat == Decimal("15")
    assert filing.diluted_eps == Decimal("8.25")
    assert filing.audit_status == "Un-Audited"


def test_bse_public_results_page_parser_reads_new_and_revised_rows():
    html = '''
    <html><body><table>
      <tr><th>Financial Year Quarter Type Status Filing_Date_Time Revised_Date_Time Revision_Reason Standalone XBRL</th></tr>
      <tr>
        <td>2025-2026</td><td>Standalone-Jun-26 Quarter</td><td>New</td><td>14-08-2026 20:01:09</td>
        <td>-</td><td>-</td><td><a href="/xbrl/abc.xml">View XBRL</a></td>
      </tr>
      <tr>
        <td>2025-2026</td><td>Consolidated-Jun-26 Quarter</td><td>Revised</td><td>15-08-2026 12:24:17</td>
        <td>-</td><td>Correction</td><td><a href="/xbrl/abc2.xml">View XBRL</a></td>
      </tr>
    </table></body></html>
    '''
    provider = BSEFinancialResultsProvider.__new__(BSEFinancialResultsProvider)
    records = provider._parse_html(html, "ABC", "https://www.bseindia.com/corporates/Comp_Results.aspx?Code=500000")
    assert len(records) == 2
    assert records[0].period_end == date(2026, 6, 30)
    assert records[0].statement_scope == "standalone"
    assert records[0].submission_type == "Original"
    assert records[1].statement_scope == "consolidated"
    assert records[1].submission_type == "Revised"


@pytest.mark.parametrize('profile,revenue,pat,basis', [
    ('BANKING', 'Income', 'ProfitLossForThePeriod', 'BANK_TOTAL_INCOME'),
    ('GI', 'OperatingIncome', 'ProfitLossAfterTax', 'GENERAL_INSURANCE_OPERATING_INCOME'),
    ('LI', 'NetPremiumIncome', 'ProfitLossAfterTaxAndExtraordinaryItems', 'LIFE_INSURANCE_NET_PREMIUM'),
])
def test_financial_sector_taxonomies_preserve_income_basis_and_quarter(profile, revenue, pat, basis):
    xml = SAMPLE_XBRL.replace(b'RevenueFromOperations', revenue.encode()).replace(
        b'ProfitLossForPeriod', pat.encode())
    record = ExchangeFilingRecord(exchange='NSE', symbol='ABC', company_name='ABC',
        period_end=date(2026, 6, 30), filed_at=datetime(2026, 8, 12),
        statement_scope='standalone', submission_type='Original',
        xbrl_uri=f'https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_{profile}_123_12082026010000_WEB.xml',
        raw_payload={'original': 'preserved'})
    filing = filing_from_exchange_record(record, xml)
    assert (filing.sales, filing.pat) == (Decimal('120'), Decimal('15'))
    assert filing.raw_payload['canonical_metric_basis']['sales'] == basis
    assert filing.raw_payload['original'] == 'preserved'
    assert 'canonical_metric_basis' not in record.raw_payload
    # Generic/foreign documents cannot activate a banking income alias.
    generic = filing_from_exchange_record(replace(record, xbrl_uri='https://example.com/bank.xml'), xml)
    assert generic.sales is None
    assert 'canonical_metric_basis' not in generic.raw_payload
