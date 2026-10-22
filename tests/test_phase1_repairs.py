from datetime import date
from types import SimpleNamespace

import pytest

from scripts.ingest_sector_classifications import _classification, fetch_classification
from scripts.ingest_module_e_benchmarks import (
    SOURCE, _number, coverage_audit, fetch_window, month_windows,
)
from scripts.local_phase1_backfill import audit_failures, date_coverage


def sector_source(*, series=None, suspended=None, meta_isin='INE123', quote_isin='INE123'):
    calls = []
    def quote(symbol, series, market_type):
        calls.append((symbol, series, market_type))
        return {'equityResponse': [{'metaData': {'symbol': symbol, 'isinCode': quote_isin},
                'secInfo': {'sector': 'Information Technology', 'industryInfo': 'IT - Services'}}]}
    return SimpleNamespace(
        equity_meta_info=lambda symbol: {'symbol': symbol, 'isin': meta_isin, 'activeSeries': series or [],
                                        'tempSuspendedSeries': suspended or [], 'marketType': 'N',
                                        'isSuspended': 'true' if suspended and not series else 'false'},
        get_detailed_scrip_data=quote,
    ), calls


def test_discovers_be_series_instead_of_requesting_eq():
    source, calls = sector_source(series=['BE'])
    result = fetch_classification(source, 'ADROITINFO', 'INE123')
    assert result['sector'] == 'Information Technology'
    assert calls == [('ADROITINFO', 'BE', 'N')]


def test_stale_metadata_isin_requires_matching_detailed_quote():
    source, _ = sector_source(series=['BE'], meta_isin='OLD', quote_isin='CURRENT')
    result = fetch_classification(source, 'HINDCON', 'CURRENT')
    assert result['isin'] == 'CURRENT'
    assert result['metadata_isin'] == 'OLD'
    with pytest.raises(RuntimeError, match='quote ISIN mismatch'):
        fetch_classification(source, 'HINDCON', 'DIFFERENT')


def test_suspended_sector_is_recoverable_and_suspension_is_preserved():
    source, calls = sector_source(suspended=['BE'])
    result = fetch_classification(source, 'LASA', 'INE123')
    assert result['is_suspended'] == 'true'
    assert calls[0][1] == 'BE'


def test_official_symbol_alias_preserves_original_lookup_and_checks_isin():
    source, calls = sector_source(series=['EQ'])
    result = fetch_classification(source, 'SILLYMONKS', 'INE123')
    assert calls[0][0] == 'CRESTO'
    assert result['requested_symbol'] == 'SILLYMONKS'
    assert result['symbol_alias']['source_url'].endswith('CML76169.pdf')


def test_empty_metadata_does_not_accept_unidentified_quote():
    source = SimpleNamespace(equity_meta_info=lambda symbol: {},
                             get_detailed_scrip_data=lambda *a, **k: {'equityResponse': [{'secInfo': {'sector': 'IT'}}]})
    with pytest.raises(RuntimeError, match='classification missing'):
        fetch_classification(source, 'ABC', 'INE123')


def test_placeholder_sector_does_not_count_as_coverage():
    assert _classification({'secInfo': {'sector': 'N/A', 'macro': '-', 'industry': 'NA'}}) == (None, None)


def test_month_windows_cover_leap_day_without_overlap():
    assert list(month_windows(date(2024, 1, 15), date(2024, 3, 2))) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]
    with pytest.raises(ValueError):
        list(month_windows(date(2024, 2, 1), date(2024, 1, 1)))


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-inf'])
def test_nonfinite_benchmark_prices_are_rejected(value):
    assert _number(value) is None


def test_window_rejects_ignored_filters_and_invalid_rows():
    source = SimpleNamespace(fetch_historical_vix_data=lambda **kw: [
        {'EOD_TIMESTAMP': '01-JAN-2018', 'EOD_CLOSE_INDEX_VAL': 12}])
    with pytest.raises(RuntimeError, match='outside'):
        fetch_window(source, 'INDIAVIX', date(2026, 1, 1), date(2026, 1, 31))
    source.fetch_historical_vix_data = lambda **kw: [{'EOD_TIMESTAMP': 'broken', 'EOD_CLOSE_INDEX_VAL': 12}]
    with pytest.raises(RuntimeError, match='rejected 1 invalid'):
        fetch_window(source, 'INDIAVIX', date(2026, 1, 1), date(2026, 1, 31))


def test_deduplicates_dates_and_normalizes_calendar_code():
    row = {'EOD_TIMESTAMP': '01-JAN-2026', 'EOD_CLOSE_INDEX_VAL': 100}
    source = SimpleNamespace(fetch_historical_index_data=lambda *a, **kw: [row, row])
    rows, diagnostic = fetch_window(source, 'NIFTY50', date(2026, 1, 1), date(2026, 1, 31))
    assert len(rows) == 1
    assert rows[0]['benchmark_code'] == 'NIFTY50'
    assert diagnostic['raw_rows'] == 2


class AuditEngine:
    def __init__(self, rows):
        self.rows = rows
    def connect(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, statement, params):
        assert "NSE_DAILY_INDEX" in str(statement)
        dates = [d for code, d in self.rows if code == params['code'] and params['start'] <= d <= params['end']]
        return SimpleNamespace(scalars=lambda: dates)


def test_calendar_finds_shared_interior_hole_even_with_current_min_max():
    sessions = [date(2026, 10, d) for d in [12, 13, 14, 15, 16]]
    rows = [(code, d) for code in ['NIFTY50', 'NIFTY500'] for d in sessions]
    for code in ['INDIAVIX', 'NIFTYMIDSMALLCAP400']:
        rows += [(code, d) for d in sessions if d.day != 14]
    audit = coverage_audit(AuditEngine(rows), date(2026, 10, 12), date(2026, 10, 18))
    for code in ['INDIAVIX', 'NIFTYMIDSMALLCAP400']:
        item = audit[code]
        assert not item['stale']
        assert item['missing_dates'] == ['2026-10-14']
        assert date_coverage(item) == 80
    assert audit['NIFTY50']['missing_sessions'] == audit['NIFTY500']['missing_sessions'] == 0
    failures = audit_failures({'sector': 2302, 'total': 2302, 'bench': audit, 'gsm': 1871, 'errors': 0})
    assert len(failures) == 2
    assert all('missing sessions' in f for f in failures)


def test_closed_market_dates_are_not_gaps():
    sessions = [date(2026, 10, 1), date(2026, 10, 5)]  # Oct 2 + weekend closed
    rows = [(code, d) for code in ['NIFTY50', 'NIFTY500', 'INDIAVIX', 'NIFTYMIDSMALLCAP400'] for d in sessions]
    audit = coverage_audit(AuditEngine(rows), date(2026, 10, 1), date(2026, 10, 5))
    assert all(v['missing_sessions'] == 0 for v in audit.values())


def test_absent_calendar_cannot_pass_audit():
    rows = [(code, date(2026, 10, 16)) for code in ['INDIAVIX', 'NIFTYMIDSMALLCAP400']]
    audit = coverage_audit(AuditEngine(rows), date(2026, 10, 1), date(2026, 10, 18))
    assert all(v['calendar_unavailable'] for v in audit.values())


def test_unrepresented_calendar_month_fails_closed():
    rows = [(code, date(2026, 10, 16)) for code in ['NIFTY50', 'INDIAVIX', 'NIFTYMIDSMALLCAP400']]
    audit = coverage_audit(AuditEngine(rows), date(2026, 9, 1), date(2026, 10, 18))
    assert all(v['calendar_unavailable'] for v in audit.values())
