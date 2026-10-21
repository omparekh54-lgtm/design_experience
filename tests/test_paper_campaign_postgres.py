"""Isolated PG17 campaign identity/immutability checks; production is never a target."""
import json,os
from pathlib import Path
from datetime import timedelta
import pytest
from sqlalchemy import text
from test_financial_ingestion_postgres import database
from packages.pms.paper_campaign import freeze_campaign,draft_manifest,active_campaign
from packages.pms.matrix_data import code_fingerprint
from packages.pms.config import load_strategy_config
from packages.pms.backtest_matrix import fingerprint

pytestmark=pytest.mark.skipif(not os.getenv('PMS_COPY_TEST_PG_URL'),reason='isolated PostgreSQL service required')


def test_migration_preserves_safety_and_frozen_campaign_cannot_be_rewritten(database):
    migration=(Path(__file__).resolve().parents[1]/'migrations/013_paper_campaign.sql').read_text()
    with database.begin() as c:
        c.execute(text('alter table pms.instruments add column nse_symbol text,add column isin text,add column active boolean default true'))
        c.execute(text("update pms.instruments set nse_symbol='TEST',isin='ISIN'"))
        c.execute(text('create table pms.safety_state(singleton boolean,execution_mode text,new_orders_enabled boolean,emergency_stop boolean,locked_reason text)'))
        c.execute(text("insert into pms.safety_state values(true,'PAPER',false,false,'reference_data_incomplete')"))
        c.execute(text(migration));c.execute(text(migration))
        assert active_campaign(c) is None
        assert c.execute(text('select locked_reason from pms.safety_state')).scalar_one()=='reference_data_incomplete'
    cfg=load_strategy_config();code=code_fingerprint()
    with database.begin() as c:
        now=c.execute(text('select transaction_timestamp()')).scalar_one()
        readiness=dict(status='READY_FOR_ACTIVATION',blockers=[],checked_at=now.isoformat(),
                       code_version=code,config_fingerprint=fingerprint(cfg),initial_capital=cfg['backtest']['initial_capital'])
        from packages.pms.paper_campaign import universe
        manifest=draft_manifest(readiness,cfg,universe(c),code_version=code)
        with pytest.raises(ValueError,match='resolved_paper_lock'):
            freeze_campaign(c,manifest,readiness=readiness,now=now)
        assert active_campaign(c) is None
    # Isolated test-only resolution; production locks are never changed by migration/freezing.
    with database.begin() as c:c.execute(text('update pms.safety_state set locked_reason=null'))
    with database.begin() as c:
        now=c.execute(text('select transaction_timestamp()')).scalar_one()
        readiness['checked_at']=now.isoformat()
        frozen=freeze_campaign(c,manifest,readiness=readiness,now=now)
        assert c.execute(text('select new_orders_enabled from pms.safety_state')).scalar_one() is False
        assert frozen['activated_at']==now.isoformat()
    for sql in ["update pms.paper_campaigns set activated_at=activated_at-interval '1 day'",
                "update pms.paper_campaigns set manifest='{}'::jsonb",'delete from pms.paper_campaigns']:
        with pytest.raises(Exception):
            with database.begin() as c:c.execute(text(sql))
    with database.begin() as c:
        assert c.execute(text('select count(*) from pms.paper_campaigns')).scalar_one()==1
        assert c.execute(text('select value from public.source_sentinel')).scalar_one()=='unchanged'
