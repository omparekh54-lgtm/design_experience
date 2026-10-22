import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from packages.data_layer.cloud_sources import SourceError, parse_shareholding


def source():
    folder = Path(__file__).with_name('source_fixtures')
    return ((folder/'councodos_2026-09-30.xml').read_bytes(),
            json.loads((folder/'councodos_2026-09-30.json').read_text()))


def test_exact_official_councodos_document_reconciles_without_rewriting_source():
    xml, record = source()
    original = copy.deepcopy(record)
    values = parse_shareholding(xml, record, expected_isin='INE695B01025')
    assert values['period_end'] == date(2026,9,30)
    assert values['filed_at'].isoformat() == '2026-10-21T15:50:16+05:30'
    assert abs(values['promoter_holding_pct'] - Decimal('53.86')) < Decimal('.1')
    assert values['total_shares'] > 0
    assert values['identity_review']['xml_isin'] == 'INE695B01027'
    assert values['identity_review']['current_isin'] == 'INE695B01025'
    assert record == original and b'INE695B01027' in xml


@pytest.mark.parametrize('change', ['body','url','period','summary_isin','symbol','expected_isin'])
def test_councodos_review_does_not_extend_to_other_documents_or_identities(change):
    xml, record = source()
    expected = 'INE695B01025'
    if change == 'body': xml += b'\n'
    elif change == 'url': record['xbrl'] += '?revised=1'
    elif change == 'period': record['date'] = '30-JUN-2026'
    elif change == 'summary_isin': record['isin'] = 'INE695B01027'
    elif change == 'symbol': record['symbol'] = 'OTHER'
    else: expected = 'INE000000000'
    with pytest.raises(SourceError):
        parse_shareholding(xml,record,expected_isin=expected)
