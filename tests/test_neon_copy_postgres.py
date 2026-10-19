"""Real pg_dump/pg_restore rehearsal, exclusively on an ephemeral CI database."""
import io
import os
import shutil
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts import copy_pms_to_neon as module


@pytest.mark.skipif(not os.getenv('PMS_COPY_TEST_PG_URL'), reason='isolated PostgreSQL17 service required')
def test_real_snapshot_archives_restore_views_constraints_sequences_and_locked_safety(monkeypatch):
    require_url = make_url(os.environ['PMS_COPY_TEST_PG_URL'])
    assert require_url.host in {'localhost', '127.0.0.1'}
    assert shutil.which('pg_dump') and shutil.which('psql')
    admin = create_engine(require_url, isolation_level='AUTOCOMMIT')
    names = ['copy_src_' + uuid4().hex, 'copy_dst_' + uuid4().hex]
    with admin.connect() as c:
        for name in names:
            c.execute(text('CREATE DATABASE ' + name))
    urls = [require_url.set(database=n).render_as_string(hide_password=False) for n in names]
    src, dst = [create_engine(u) for u in urls]
    with src.begin() as c:
        c.execute(text('CREATE SCHEMA pms'))
        for table in sorted(module.PUBLIC):
            c.execute(text(f'CREATE TABLE public.{table}(id bigserial PRIMARY KEY,payload jsonb NOT NULL)'))
            c.execute(text(f"INSERT INTO public.{table}(payload) VALUES ('{{\"quoted\":\"value\",\"unicode\":\"नमस्ते\"}}'),('{{}}')"))
        c.execute(text('ALTER TABLE public.market_bars ADD COLUMN security_id bigint REFERENCES public.securities(id)'))
        c.execute(text('UPDATE public.market_bars SET security_id=1'))
        c.execute(text('CREATE TABLE pms.instruments(id bigserial PRIMARY KEY,security_id bigint REFERENCES public.securities(id))'))
        c.execute(text('INSERT INTO pms.instruments(security_id) VALUES(1)'))
        c.execute(text("CREATE TABLE pms.safety_state(execution_mode text,new_orders_enabled boolean)"))
        c.execute(text("INSERT INTO pms.safety_state VALUES('PAPER',false)"))
        c.execute(text('CREATE TABLE pms.audit_log(id bigserial PRIMARY KEY,event text)'))
        c.execute(text("INSERT INTO pms.audit_log(event) VALUES('source evidence')"))
        c.execute(text("""CREATE FUNCTION pms.audit_operational_change() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN INSERT INTO pms.audit_log(event) VALUES ('trigger fired'); RETURN NEW; END $$"""))
        c.execute(text('CREATE TRIGGER audit_change AFTER INSERT ON pms.instruments FOR EACH ROW EXECUTE FUNCTION pms.audit_operational_change()'))
        c.execute(text('ALTER TABLE public.securities ENABLE ROW LEVEL SECURITY'))
        c.execute(text('CREATE POLICY policy_to_omit ON public.securities TO PUBLIC USING(true)'))
        # Connect every reviewed public dependency through views, as in the real source.
        for table in sorted(module.PUBLIC):
            c.execute(text(f'CREATE VIEW pms.view_{table} AS SELECT * FROM public.{table}'))
    class Pages:
        def paginate(self, **kw): return [{}]
    class Store:
        blobs = {}
        def get_paginator(self, name): return Pages()
        def upload_file(self, filename, bucket, key, **kw): self.blobs[key] = Path(filename).read_bytes()
        def get_object(self, Bucket, Key): return {'Body': io.BytesIO(self.blobs[Key])}
    storage = Store()
    monkeypatch.setattr(module, 'archive_client', lambda: storage)
    report = {}
    try:
        module.copy(*urls, src, dst, report)
        assert report['status'] == 'COPIED_DATA_MATCHED' and report['data_copied'] and not report['cutover']
        assert len(report['private_backups']) == 3 and len(storage.blobs) == 3
        with dst.begin() as c:
            assert c.execute(text('SELECT count(*) FROM pms.audit_log')).scalar_one() == 1
            assert not c.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname='public'")).scalar_one()
            assert c.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='public.securities'::regclass")).scalar_one()
            assert not c.execute(text("""SELECT count(*) FROM pg_namespace n,
              LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
              WHERE n.nspname='pms' AND a.grantee=0 AND a.privilege_type='USAGE'""")).scalar_one()
            with pytest.raises(Exception):
                c.execute(text('INSERT INTO pms.instruments(security_id) VALUES(99999)'))
        with dst.begin() as c:
            next_id = c.execute(text('INSERT INTO pms.instruments(security_id) VALUES(1) RETURNING id')).scalar_one()
            assert next_id > 1
            assert c.execute(text('SELECT count(*) FROM pms.audit_log')).scalar_one() == 2
        with pytest.raises(module.CopyBlocked, match='DESTINATION_NOT_EMPTY'):
            module.copy(*urls, src, dst, {})
    finally:
        src.dispose(); dst.dispose(); admin.dispose()


@pytest.mark.skipif(not os.getenv('PMS_COPY_TEST_PG_URL'), reason='isolated PostgreSQL17 service required')
def test_share_view_ties_keep_values_and_local_ids_across_storage_and_plan_changes():
    url = make_url(os.environ['PMS_COPY_TEST_PG_URL'])
    assert url.host in {'localhost', '127.0.0.1'}
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'share_view_' + uuid4().hex
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE ' + name))
    engine = create_engine(url.set(database=name))
    migration = Path('migrations/011_deterministic_share_view_ids.sql').read_text()
    try:
        with engine.begin() as c:
            c.execute(text('CREATE SCHEMA pms'))
            c.execute(text('CREATE TABLE pms.instruments(id bigint PRIMARY KEY,external_security_id bigint UNIQUE)'))
            c.execute(text('INSERT INTO pms.instruments VALUES(1,100)'))
            c.execute(text('''CREATE TABLE public.sources(id bigint PRIMARY KEY,metadata jsonb,
                source_type text,published_at timestamptz,retrieved_at timestamptz)'''))
            c.execute(text("INSERT INTO public.sources VALUES(1,'{}','NSE','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"))
            c.execute(text('''CREATE TABLE public.financial_facts(id bigint PRIMARY KEY,security_id bigint,
                period_end date,source_id bigint,value numeric,fact_name text,created_at timestamptz,
                data jsonb,period_type text)'''))
            # Annual/quarter facts have the same old ordering keys but distinct payloads.
            c.execute(text("""INSERT INTO public.financial_facts VALUES
                (20,100,'2025-12-31',1,200,'shares_outstanding','2026-01-01T00:00:00Z','{}','annual'),
                (10,100,'2025-12-31',1,100,'shares_outstanding','2026-01-01T00:00:00Z','{}','quarter')"""))
            c.execute(text('''CREATE TABLE pms.shares_outstanding_history_local(id bigint PRIMARY KEY,
                instrument_id bigint,effective_from date,effective_to date,shares_outstanding numeric,
                source text,knowable_at timestamptz,raw_payload jsonb)'''))
            c.execute(text("""INSERT INTO pms.shares_outstanding_history_local VALUES
                (7,1,'2025-01-01',NULL,75,'LOCAL','2025-01-01T00:00:00Z','{}')"""))
            c.execute(text(migration))
            query = text('SELECT id,shares_outstanding FROM pms.shares_outstanding_history ORDER BY id')
            expected = [(7,75),(1000000000001,100),(1000000000002,200)]
            assert [tuple(r) for r in c.execute(query)] == expected
            c.execute(text('CREATE INDEX reverse_facts ON public.financial_facts(security_id,period_end,source_id,id DESC)'))
            c.execute(text('CLUSTER public.financial_facts USING reverse_facts'))
            c.execute(text('SET LOCAL enable_seqscan=off'))
            c.execute(text('SET LOCAL enable_hashjoin=off'))
            assert [tuple(r) for r in c.execute(query)] == expected
            c.execute(text(migration))
            assert [tuple(r) for r in c.execute(query)] == expected
            assert c.execute(text('SELECT count(*) FROM public.financial_facts')).scalar_one() == 2
            assert c.execute(text('SELECT id FROM pms.shares_outstanding_history_local')).scalar_one() == 7
    finally:
        engine.dispose(); admin.dispose()
