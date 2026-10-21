from datetime import date
from types import SimpleNamespace
import pytest
from packages.data_layer.cloud_sources import SourceError
from scripts import verify_cloud_catch_up_sources as verify


def test_pending_merge_retains_changed_revisions_without_duplicate_retries():
    a = {'symbol': 'TEST', 'revision': 1}
    b = {'symbol': 'TEST', 'revision': 2}
    assert verify.merge_pending([a], [a, b]) == [a, b]


def test_classification_preflight_checks_unstamped_and_refresh_due_records():
    day = date(2026, 10, 20)
    row = {'sector': 'Services', 'metadata': {}}
    assert verify.classification_due(row, day)
    for observed, due in [('2026-10-19T10:00:00+05:30', False), ('2026-01-01T10:00:00+05:30', True)]:
        row['metadata'] = {'sector_classification': {'observed_at': observed}}
        assert verify.classification_due(row, day) == due


def test_preflight_errors_are_fixed_codes_without_exception_payload():
    class Budget:
        def http(self):
            raise ValueError('private details must not be published')
    result = verify.verify_item(('classification', {'symbol': 'TEST'}), {}, Budget(), None)
    assert result == {'kind': 'classification', 'symbol': 'TEST',
                      'code': 'preflight_parser_unavailable', 'error_type': 'ValueError'}


def test_preflight_requires_readonly_transaction_before_source_requests(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a): return SimpleNamespace(scalar_one=lambda: 'off')
    engine = SimpleNamespace(connect=Connection)
    monkeypatch.setattr(verify, 'check_database', lambda e: None)
    monkeypatch.setattr(verify.CloudStore, 'instruments', lambda self: {})
    with pytest.raises(SourceError, match='preflight_read_only_required'):
        verify.verify(engine, date(2026, 10, 19),
                      now=verify.datetime(2026, 10, 20, 18, tzinfo=verify.IST))



def test_repeated_source_denials_stop_new_downloads(monkeypatch):
    budget = verify.SourceBudget()
    calls = []
    def denied(self, url):
        calls.append(url)
        raise SourceError('source_http_403')
    monkeypatch.setattr(verify.OfficialHTTP, 'fetch', denied)
    http = budget.http()
    for i in range(3):
        with pytest.raises(SourceError, match='source_http_403'):
            http.fetch(f'https://nsearchives.nseindia.com/{i}.xml')
    with pytest.raises(SourceError, match='preflight_source_access_denied'):
        http.fetch('https://www.nseindia.com/api/next')
    assert len(calls) == 3 and budget.blocked


def test_new_listing_projection_is_readonly_and_still_rejects_identity_conflicts():
    known = {'OLD': {'isin': 'INE000A01010', 'sector': 'Services', 'id': 1}}
    master = {'OLD': {'isin': 'INE000A01010'},
              'NEW': {'isin': 'INE111A01010', 'listed_from': date(2026, 10, 21)}}
    result = verify.projected_master(known, master)
    assert set(result) == {'OLD', 'NEW'} and 'NEW' not in known
    assert result['OLD']['id'] == 1 and 'id' not in result['NEW']
    assert verify.classification_due(result['NEW'], date(2026, 10, 21))
    for bad in [{'OLD': {'isin': 'INE999A01010'}},
                {'RENAMED': {'isin': 'INE000A01010'}},
                {'NEW1': {'isin': 'INE111A01010'}, 'NEW2': {'isin': 'INE111A01010'}}]:
        with pytest.raises(SourceError, match='preflight_master_identity_unresolved'):
            verify.projected_master(known, bad)


def test_current_cash_equity_scope_defers_future_listings_and_excludes_rights():
    from packages.data_layer.cloud_sources import cash_equity_master
    rows = {
        'TODAY': dict(symbol='TODAY', name='Today Ltd', listed_from=date(2026, 10, 21)),
        'FUTURE': dict(symbol='FUTURE', name='Future Ltd', listed_from=date(2026, 10, 22)),
        'TEST-RE': dict(symbol='TEST-RE', name='Test Ltd-RE', listed_from=date(2026, 10, 21)),
    }
    current, excluded = cash_equity_master(rows, date(2026, 10, 21))
    assert set(current) == {'TODAY'} and len(rows) == 3
    assert {r['reason'] for r in excluded} == {
        'LISTING_NOT_YET_EFFECTIVE', 'RIGHTS_ENTITLEMENT_OUTSIDE_CASH_EQUITY_UNIVERSE'}
    tomorrow, _ = cash_equity_master(rows, date(2026, 10, 22))
    assert set(tomorrow) == {'TODAY', 'FUTURE'}
    historical, _ = cash_equity_master(rows, date(2026, 10, 19))
    assert historical == {}


def test_classification_writer_defers_later_listings_without_requesting_sources():
    store = verify.CloudStore(None)
    store.instruments = lambda: {'NEW': {'listed_from': date(2026, 10, 21)}}
    # No engine or HTTP is available: a later listing must not trigger either.
    result = store.classifications(None, date(2026, 10, 21), limit=100,
                                   listed_through=date(2026, 10, 19))
    assert result['due'] == result['remaining'] == 0
    store.instruments = lambda: {'FUTURE': {'listed_from': date(2026, 10, 22)}}
    assert store.classifications(None, date(2026, 10, 21), limit=100)['remaining'] == 0
