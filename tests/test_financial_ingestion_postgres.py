"""Isolated real-PostgreSQL tests; never connect these fixtures to application DBs."""
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from packages.data_layer.cloud_storage import CloudStore
from packages.data_layer.postgres_storage import PostgresStorage
from packages.data_layer.exchange_financials import filings_from_exchange_record
from test_financial_ingestion_v2 import RECORD, xml_doc, fact

pytestmark = pytest.mark.skipif(not os.getenv('PMS_COPY_TEST_PG_URL'), reason='isolated PostgreSQL service required')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database():
    url = make_url(os.environ['PMS_COPY_TEST_PG_URL'])
    assert url.host in {'127.0.0.1','localhost'}
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'financial_ingestion_' + uuid4().hex
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE '+name))
    engine = create_engine(url.set(database=name), connect_args={'options':'-c search_path=pms,public'})
    try:
        with engine.begin() as c:
            c.execute(text('CREATE SCHEMA pms'))
            c.execute(text('CREATE TABLE pms.instruments(id bigserial PRIMARY KEY,exchange text,trading_symbol text)'))
            c.execute(text("INSERT INTO pms.instruments(exchange,trading_symbol) VALUES('NSE','TEST-EQ')"))
            c.execute(text('''CREATE TABLE pms.financial_filings(id bigserial PRIMARY KEY,
                instrument_id bigint REFERENCES pms.instruments(id),period_end date,filing_timestamp timestamptz,
                source text,consolidated boolean,revision_number int,ingested_at timestamptz DEFAULT now())'''))
            c.execute(text('''CREATE TABLE pms.financial_facts(filing_id bigint REFERENCES pms.financial_filings(id),
                fact_name text,value numeric,unit text,PRIMARY KEY(filing_id,fact_name))'''))
            c.execute(text((ROOT/'migrations/003_exchange_financials.sql').read_text()))
            c.execute(text('CREATE TABLE public.source_sentinel(id int PRIMARY KEY,value text)'))
            c.execute(text("INSERT INTO public.source_sentinel VALUES(1,'unchanged')"))
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text('DROP DATABASE '+name+' WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize('overlay', [False,True])
def test_atomic_quarter_year_upsert_reparse_and_migration_preserve_source(database, overlay):
    if overlay:
        with database.begin() as c:
            for table in ['financial_filings','financial_facts']:
                c.execute(text(f'ALTER TABLE pms.{table} RENAME TO {table}_local'))
                c.execute(text(f'CREATE VIEW pms.{table} AS SELECT * FROM pms.{table}_local'))
        store = CloudStore(database)
        store._known = {'TEST':{'id':1}}
        store._tables = {'financial_filings':'financial_filings_local','financial_facts':'financial_facts_local'}
        write = store.financial
    else:
        store = PostgresStorage(engine=database)
        write = lambda rows: store.append_filings(rows,refresh_module_a=False)
    migration = (ROOT/'migrations/012_financial_period_identity.sql').read_text()
    with database.begin() as c:
        c.execute(text(migration))
        c.execute(text(migration))  # Idempotent.
    x = xml_doc([fact('RevenueFromOperations',30_0000000),fact('ProfitLossForPeriod',10_0000000),
        fact('RevenueFromOperations',300_0000000,'Y'),fact('ProfitLossForPeriod',100_0000000,'Y'),
        fact('CashFlowsFromUsedInOperatingActivities',80_0000000,'Y')])
    rows = filings_from_exchange_record(RECORD,x)
    write(rows)
    write(rows)
    with database.connect() as c:
        assert c.execute(text('SELECT count(*) FROM pms.financial_filings')).scalar_one() == 2
        assert [tuple(r) for r in c.execute(text("SELECT f.filing_kind,v.value FROM pms.financial_filings f JOIN pms.financial_facts v ON v.filing_id=f.id WHERE v.fact_name='pat' ORDER BY f.filing_kind"))] == [('annual',100),('quarter',10)]
    # Reparse a formerly selected field that is now conflicting. The stale
    # selected sales must disappear, while the original filing row stays.
    conflicting = x.replace(b'</xbrl>',fact('RevenueFromOperations',999_0000000).encode()+b'</xbrl>')
    write(filings_from_exchange_record(RECORD,conflicting))
    with database.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pms.financial_facts v JOIN pms.financial_filings f ON f.id=v.filing_id WHERE f.filing_kind='quarter' AND v.fact_name='sales'")).scalar_one() == 0
        assert c.execute(text('SELECT count(*) FROM pms.financial_filings')).scalar_one() == 2
        assert c.execute(text('SELECT value FROM public.source_sentinel')).scalar_one() == 'unchanged'

    bad = [replace(rows[0], filed_at=datetime(2024,6,1)),
           replace(rows[1], filed_at=datetime(2024,6,1), raw_payload={"not_json": {1}})]
    with pytest.raises(TypeError):
        write(bad)
    with database.connect() as c:
        assert c.execute(text('SELECT count(*) FROM pms.financial_filings')).scalar_one() == 2
