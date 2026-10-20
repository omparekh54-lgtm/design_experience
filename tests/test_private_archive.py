import hashlib
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from packages.data_layer.cloud_sources import Document, OfficialHTTP, SourceError
from packages.data_layer.private_archive import PrivateArchive, PROBE_KEY, PROBE_BODY


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class Store:
    def __init__(self):
        self.objects = {}
        self.reads = 0
        self.corrupt = False

    def put_object(self, Bucket, Key, Body, Metadata=None, **kwargs):
        self.objects[Key] = (Body, Metadata or {})

    def get_object(self, Bucket, Key):
        self.reads += 1
        return {'Body': io.BytesIO(b'corrupt' if self.corrupt else self.objects[Key][0])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing('private credential must never appear in output')
        body, metadata = self.objects[Key]
        return {'ContentLength': len(body), 'Metadata': metadata}

    def get_paginator(self, name):
        return SimpleNamespace(paginate=lambda **kw: [{'Contents': [
            {'Size': len(v[0])} for v in self.objects.values()]}])


def doc(body=b'official XML evidence'):
    return Document('https://nsearchives.nseindia.com/corporate/test.xml', body,
                    hashlib.sha256(body).hexdigest(), datetime(2026,10,20,tzinfo=timezone.utc), len(body))


def test_private_roundtrip_is_verified_and_duplicate_body_is_not_uploaded_twice():
    store = Store()
    archive = PrivateArchive(store, privacy_check=lambda url: True)
    key = archive.put(doc())
    reads = store.reads
    assert archive.put(doc()) == key
    assert store.reads == reads
    assert archive.summary()['objects_uploaded'] == 1
    assert archive.summary()['objects_verified'] == 1
    assert store.objects[key][0] == doc().body
    assert key.startswith('raw-sources/2026-10-20/')


def test_public_or_unverifiable_bucket_stops_before_any_raw_upload():
    store = Store()
    with pytest.raises(SourceError, match='archive_privacy_not_verified'):
        PrivateArchive(store, privacy_check=lambda url: False)
    assert set(store.objects) == {PROBE_KEY}


def test_corrupt_download_fails_before_claiming_archive_success():
    store = Store()
    archive = PrivateArchive(store, privacy_check=lambda url: True)
    store.corrupt = True
    with pytest.raises(SourceError, match='archive_checksum_mismatch'):
        archive.put(doc())
    assert archive.summary()['objects_verified'] == 0


def test_preexisting_mismatch_is_preserved_instead_of_overwritten():
    store = Store()
    archive = PrivateArchive(store, privacy_check=lambda url: True)
    key = archive.put(doc())
    store.objects[key] = (b'wrong', {})
    fresh = PrivateArchive(store, privacy_check=lambda url: True)
    with pytest.raises(SourceError, match='archive_existing_object_mismatch'):
        fresh.put(doc())
    assert store.objects[key][0] == b'wrong'


def test_capacity_reserve_stops_upload_and_preserves_existing_objects():
    store = Store()
    archive = PrivateArchive(store, privacy_check=lambda url: True,
                             limit=len(PROBE_BODY)+len(doc().body))
    with pytest.raises(SourceError, match='archive_capacity_limit'):
        archive.put(doc())
    assert set(store.objects) == {PROBE_KEY}


@pytest.mark.parametrize('value', ['', 'not-json-private-secret', '[]',
    '{"access_key_id":1,"secret_access_key":"private-secret"}'])
def test_invalid_credentials_are_rejected_without_echo(monkeypatch, value):
    monkeypatch.setenv('PMS_NEON_ARCHIVE_JSON', value)
    with pytest.raises(SourceError, match='^archive_credential_configuration_invalid$'):
        PrivateArchive.from_env()


def test_http_does_not_release_document_when_private_archival_fails():
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return 'https://www.nseindia.com/api/test'
        def read(self, n): return b'[]'
    class Archive:
        def put(self, document): raise SourceError('archive_write_or_verify_failed')
    http = OfficialHTTP(opener=SimpleNamespace(open=lambda *a, **kw: Response()), archive=Archive())
    with pytest.raises(SourceError, match='archive_write_or_verify_failed'):
        http.fetch('https://www.nseindia.com/api/test')
    assert http.documents == []


def test_http_retains_archive_reference_and_discards_body_in_receipt():
    store = Store()
    archive = PrivateArchive(store, privacy_check=lambda url: True)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return 'https://www.nseindia.com/api/test'
        def read(self, n): return b'[]'
    http = OfficialHTTP(opener=SimpleNamespace(open=lambda *a, **kw: Response()), archive=archive)
    result = http.fetch('https://www.nseindia.com/api/test')
    assert result.archive_key in store.objects
    assert http.documents[0].body == b'' and http.documents[0].archive_key == result.archive_key


def test_production_run_refuses_missing_archive_before_database_access():
    from packages.pms.cloud_collection import run_collection
    with pytest.raises(SourceError, match='private_archive_required'):
        run_collection(object(), require_archive=True)
