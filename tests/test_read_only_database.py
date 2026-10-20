import pytest
from packages.data_layer.read_only import readonly_connections

def test_real_postgres_preflight_session_rejects_writes():
    import os
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError
    url = os.environ.get('PMS_COPY_TEST_PG_URL')
    if not url:
        pytest.skip('Isolated PostgreSQL integration service required')
    assert make_url(url).host in {'127.0.0.1', 'localhost'}
    engine = create_engine(url)
    readonly_connections(engine)
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            assert connection.execute(text('SELECT 1')).scalar_one() == 1
            with pytest.raises(DBAPIError) as caught:
                connection.execute(text('CREATE TABLE public.pms_preflight_must_not_write(id integer)'))
            assert getattr(caught.value.orig, 'pgcode', None) == '25006'
            connection.rollback()
    finally:
        engine.dispose()
