import json
from datetime import datetime

import pytest

from packages.data_layer import classification_identity
from packages.data_layer.cloud_sources import Document, IST, SourceError, fetch_classification


def doc(value):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    from hashlib import sha256
    return Document('https://www.nseindia.com/api/test', body, sha256(body).hexdigest(), datetime.now(IST), len(body))


@pytest.mark.parametrize('change', [None, 'metadata', 'symbol', 'isin', 'series', 'company', 'quote_isin', 'quote_market'])
@pytest.mark.parametrize('multiple_series', [False, True])
def test_reviewed_stale_metadata_requires_exact_review_and_live_master(monkeypatch, change, multiple_series):
    metadata = dict(symbol='TEST', companyName='Test Limited', isin='INE000A01010', activeSeries=['BE'], marketType='G')
    review = dict(symbol='TEST', company='Test Limited', metadata_isin=metadata['isin'],
                  current_isin='INE000A01028', series='BE', metadata_sha256=doc(metadata).sha256)
    monkeypatch.setattr(classification_identity, 'reviews', lambda: [review])
    master = dict(symbol='TEST', isin='INE000A01028', series='BE', company='Test Limited')
    if change in master:
        master[change] = 'INE999A01010' if change == 'isin' else 'OTHER'
    if change == 'metadata':
        metadata['isSuspended'] = 'false'  # Even an otherwise benign change requires review.
    class HTTP:
        def api(self, path, **params):
            if params['functionName'] == 'getMetaData':
                return doc(metadata)
            return doc({'equityResponse': [{
                'metaData': dict(symbol='TEST', isinCode='INE999A01010' if change == 'quote_isin' else None,
                                 series='BE', marketType='N' if change == 'quote_market' else 'G'),
                'secInfo': dict(sector='Capital Goods')}]})
        def fetch(self, url):
            assert url == 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv'
            extra = ('{symbol},{company},BZ,04-Aug-2020,{isin}\n'.format(**master)
                     if multiple_series else '')
            return doc(('SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,ISIN NUMBER\n'
                        '{symbol},{company},{series},04-Aug-2020,{isin}\n'.format(**master) + extra).encode())
    if change is None:
        _, classification = fetch_classification(HTTP(), 'TEST', 'INE000A01028')
        assert classification['identity_basis'] == 'REVIEWED_METADATA_LIVE_MASTER_AND_QUOTE'
        assert classification['identity_review']['verified_master_sha256']
        assert classification['identity_review']['metadata_isin'] == metadata['isin']
    else:
        with pytest.raises(SourceError, match='classification_identity_mismatch'):
            fetch_classification(HTTP(), 'TEST', 'INE000A01028')


def test_review_manifest_only_covers_the_two_observed_metadata_exceptions():
    rows = classification_identity.reviews()
    assert {r['symbol'] for r in rows} == {'SUPREMEENG', 'TIRUPATIFL'}
    assert all(len(r['metadata_sha256']) == 64 and r['current_isin'] != r['metadata_isin'] for r in rows)
