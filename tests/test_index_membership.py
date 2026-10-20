import pytest

from packages.data_layer.cloud_sources import SourceError
from packages.data_layer.index_membership import REVIEWED_DUMMY, equity_membership


EQUITY = {'Company Name': 'Example', 'Industry': 'Industrial', 'Symbol': 'TEST',
          'Series': 'EQ', 'ISIN Code': 'INE000A01010'}
KNOWN = {'TEST': {'isin': 'INE000A01010'}}


def test_official_snapshot_retains_explicit_non_equity_exclusions():
    reit = {**EQUITY, 'Symbol': 'REIT', 'Series': 'RR', 'ISIN Code': 'INE001A25010'}
    included, excluded = equity_membership([EQUITY, reit, REVIEWED_DUMMY], KNOWN)
    assert included == [EQUITY]
    assert [r['reason'] for r in excluded] == [
        'REIT_OUTSIDE_CASH_EQUITY_UNIVERSE', 'REVIEWED_NON_TRADABLE_INDEX_ADJUSTMENT']
    assert excluded[1]['ISIN Code'] == 'DUM545A01024'


@pytest.mark.parametrize('row', [
    {**EQUITY, 'Symbol': 'UNKNOWN'}, {**EQUITY, 'ISIN Code': 'INE001A01010'},
    {**REVIEWED_DUMMY, 'ISIN Code': 'DUM545A01025'},
    {**REVIEWED_DUMMY, 'Company Name': 'Different issuer'},
    {**EQUITY, 'Series': 'XX'},
])
def test_scope_policy_does_not_waive_unknown_equity_or_changed_dummy(row):
    with pytest.raises(SourceError, match='identity_unresolved'):
        equity_membership([EQUITY, row] if row['Symbol'] != 'TEST' else [row], KNOWN)


def test_duplicate_constituents_and_empty_equity_projection_block():
    with pytest.raises(SourceError, match='duplicate'):
        equity_membership([EQUITY, EQUITY], KNOWN)
    with pytest.raises(SourceError, match='empty_equity'):
        equity_membership([REVIEWED_DUMMY], KNOWN)
