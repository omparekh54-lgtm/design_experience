"""Destructive boundary and credential-leak regressions for the one-shot copy."""
import os
import subprocess
from pathlib import Path
import pytest
from sqlalchemy.engine import make_url
from scripts.copy_pms_to_neon import (
    TARGET_HOST, CopyBlocked, connection_url, filtered_toc, pg_environment,
    private_backup, verify_sequences,
)


def target(host=TARGET_HOST, database='pms'):
    return f'postgresql://pms_owner:private-password@{host}/{database}?sslmode=require'


@pytest.mark.parametrize('raw', [target(TARGET_HOST.replace('.c-3', '-pooler.c-3')),
    target('other.neon.tech'), target(database='production'),
    target().replace('pms_owner:', 'other:'), target() + '&sslmode=disable'])
def test_destination_is_pinned_and_ambiguous_tls_is_rejected(raw):
    with pytest.raises((CopyBlocked, ValueError)):
        connection_url(raw, destination=True)


def test_full_tls_and_no_password_in_process_arguments(monkeypatch):
    u = connection_url(target(), destination=True)
    assert make_url(u).query['sslmode'] == 'verify-full'
    monkeypatch.setenv('PGSSLMODE', 'disable')
    monkeypatch.setenv('PGSERVICE', 'untrusted')
    env, dsn = pg_environment(u)
    assert 'private-password' not in dsn
    assert env['PGPASSWORD'] == 'private-password'
    assert 'PGSSLMODE' not in env and 'PGSERVICE' not in env
    assert make_url(dsn).query['sslmode'] == 'verify-full'


def test_source_is_pinned_to_existing_project_session_connection():
    with pytest.raises(CopyBlocked):
        connection_url('postgresql://postgres:private@aws-0-ap-south-1.pooler.supabase.com/postgres')
    with pytest.raises(CopyBlocked):
        connection_url('postgresql://postgres.cxrkicqggsuylirzohqw:private@aws-0-ap-south-1.pooler.supabase.com:6543/postgres')
    u = connection_url('postgresql://postgres.cxrkicqggsuylirzohqw:private@aws-0-ap-south-1.pooler.supabase.com:5432/postgres')
    assert make_url(u).query['sslrootcert'].endswith('supabase-prod-ca-2021.crt')


def test_placeholder_diagnostic_identifies_the_setting_without_its_value():
    with pytest.raises(CopyBlocked, match='^DATABASE_PASSWORD_PLACEHOLDER$'):
        connection_url('postgresql://postgres:%5BYOUR-PASSWORD%5D@db.cxrkicqggsuylirzohqw.supabase.co/postgres')


def test_destination_is_probed_even_when_source_configuration_is_invalid(monkeypatch):
    import scripts.copy_pms_to_neon as m
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): return Result(170011 if 'server_version_num' in str(sql) else 0)
    class Engine:
        disposed = False
        def connect(self): return Connection()
        def dispose(self): self.disposed = True
    engine = Engine()
    monkeypatch.setenv('NEON_PMS_DATABASE_URL', target())
    monkeypatch.setenv('PMS_DATABASE_URL', 'postgresql://postgres:%5BYOUR-PASSWORD%5D@db.cxrkicqggsuylirzohqw.supabase.co/postgres')
    monkeypatch.setattr(m, 'online_engine', lambda *args, **kw: engine)
    report = {}
    with pytest.raises(CopyBlocked, match='PASSWORD_PLACEHOLDER'):
        m.run('check', report)
    assert report['destination_connection_verified_full'] is True
    assert report['stage'] == 'source_configuration' and engine.disposed


def test_supabase_policies_removed_but_rls_and_constraints_retained(tmp_path, monkeypatch):
    import scripts.copy_pms_to_neon as m
    monkeypatch.setattr(m, 'command', lambda args: b'''1; 0 1 TABLE public securities postgres
2; 0 2 POLICY public securities securities_authenticated_read postgres
3; 0 3 ROW SECURITY public securities postgres
4; 0 4 FK CONSTRAINT pms instruments key postgres
5; 0 5 COMMENT public TABLE securities postgres
''')
    out = tmp_path/'toc'
    filtered_toc(tmp_path/'archive', out)
    assert 'POLICY ' not in out.read_text() and 'COMMENT ' not in out.read_text()
    assert 'ROW SECURITY ' in out.read_text() and 'FK CONSTRAINT ' in out.read_text()


def test_sequence_state_never_regresses():
    before = {'pms.id_seq': {'last_value': 42, 'is_called': True}}
    verify_sequences(before, before)
    for bad in ({}, {'pms.id_seq': {'last_value': 41, 'is_called': True}},
                {'pms.id_seq': {'last_value': 42, 'is_called': False}}):
        with pytest.raises(CopyBlocked):
            verify_sequences(bad, before)


def test_private_backup_requires_roundtrip_hash_before_restore(tmp_path):
    import io
    class Pages:
        def paginate(self, **kw): return [{}]
    class Store:
        def get_paginator(self, name): return Pages()
        def upload_file(self, *args, **kw): pass
        def get_object(self, **kw): return {'Body': io.BytesIO(b'corrupt')}
    archive = tmp_path/'backup.dump'; archive.write_bytes(b'original')
    with pytest.raises(CopyBlocked, match='CHECKSUM_MISMATCH'):
        private_backup(Store(), [archive], 'test')


def test_missing_configuration_receipt_never_contains_credentials(tmp_path):
    import sys, json
    env = os.environ.copy(); env['PMS_DATABASE_URL'] = 'not-a-url-with-private-password'
    r = subprocess.run([sys.executable, 'scripts/copy_pms_to_neon.py', '--output', str(tmp_path/'receipt.json')],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 2
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    assert receipt['status'] == 'BLOCKED' and not receipt['cutover']
    assert 'private-password' not in r.stdout + r.stderr + json.dumps(receipt)
