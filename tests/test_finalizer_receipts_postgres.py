"""Receipt restore/corruption tests on an isolated localhost PostgreSQL database.

Uses the existing PMS_COPY_TEST_PG_URL harness; never use an application DB URL.
"""
from datetime import date
import json

import pytest
from sqlalchemy import text

from test_financial_ingestion_postgres import database, pytestmark
from scripts.backfill_official_historical_prices import _db_completed_dates, price_state_sql
from scripts.backfill_official_historical_financials import _db_completed_symbols, _record_symbol_complete

START, END, DAY = date(2023,1,1), date(2024,12,31), date(2023,1,2)


def prepare(engine):
    with engine.begin() as c:
        c.execute(text('ALTER TABLE pms.instruments ADD COLUMN nse_symbol text'))
        c.execute(text("UPDATE pms.instruments SET nse_symbol='TEST'"))
        c.execute(text('''CREATE TABLE pms.provider_snapshots(id bigserial primary key,
            provider text,dataset text,content_sha256 text,source_uri text,metadata jsonb)'''))
        c.execute(text('''CREATE TABLE pms.cloud_daily_prices(instrument_id bigint,trade_date date,
            open numeric,high numeric,low numeric,close numeric,volume numeric,turnover numeric,
            source text,ingested_at timestamptz default now())'''))


def test_deleted_or_changed_prices_invalidate_only_affected_day(database):
    prepare(database)
    with database.begin() as c:
        c.execute(text("""INSERT INTO pms.cloud_daily_prices(instrument_id,trade_date,open,high,low,close,volume,turnover,source)
            VALUES(1,'2023-01-02',100,105,99,103,1000,103000,'NSE_UDIFF'),
                  (2,'2023-01-02',100,105,99,103,1000,103000,'NSE_UDIFF')"""))
        digest = c.execute(text(price_state_sql('cloud_daily_prices')), {'day':DAY,'source':'NSE_UDIFF'}).scalar_one()
        c.execute(text("""INSERT INTO pms.provider_snapshots(provider,dataset,content_sha256,source_uri,metadata)
            VALUES('NSE','HISTORICAL_NSE_PRICES',:sha,'https://archives.nseindia.com/content/test.zip',cast(:meta as jsonb))"""),
            {'sha':'a'*64,'meta':json.dumps({'complete':True,'written':2,'trade_date':str(DAY),'stored_db_digest':digest})})
    completed = lambda: _db_completed_dates(database, START, END, price_table='cloud_daily_prices')
    assert completed() == {str(DAY)}
    with database.begin() as c: c.execute(text('UPDATE pms.cloud_daily_prices SET close=104 WHERE instrument_id=2'))
    assert completed() == set()
    with database.begin() as c: c.execute(text('UPDATE pms.cloud_daily_prices SET close=103 WHERE instrument_id=2'))
    assert completed() == {str(DAY)}
    with database.begin() as c: c.execute(text('DELETE FROM pms.cloud_daily_prices WHERE instrument_id=2'))
    assert completed() == set()


def test_deleted_filing_or_fact_invalidates_symbol_completion(database):
    prepare(database)
    with database.begin() as c:
        c.execute(text("""INSERT INTO pms.financial_filings(instrument_id,period_end,filing_timestamp,source)
            VALUES(1,'2023-03-31','2023-05-01 12:00+05:30','NSE')"""))
        c.execute(text("INSERT INTO pms.financial_facts VALUES(1,'sales',100,'INR')"))
        _record_symbol_complete(c,'TEST',START,END,0,8,1,1)
    completed = lambda: _db_completed_symbols(database,START,END,0,8)
    assert completed() == {'TEST'}
    with database.begin() as c: c.execute(text('DELETE FROM pms.financial_facts'))
    assert completed() == set()
    with database.begin() as c: c.execute(text("INSERT INTO pms.financial_facts VALUES(1,'sales',100,'INR')"))
    assert completed() == {'TEST'}
    with database.begin() as c:
        c.execute(text('DELETE FROM pms.financial_facts'))
        c.execute(text('DELETE FROM pms.financial_filings'))
    assert completed() == set()
