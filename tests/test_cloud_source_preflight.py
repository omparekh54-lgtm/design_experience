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
        verify.verify(engine, date(2026, 10, 19))
