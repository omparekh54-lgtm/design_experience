"""Rollback-only integration check against an explicitly selected test database.

PMS_TEST_DATABASE_URL must point to a migrated Postgres database. The Oct 18
validation also executed these persistence assertions via the connected DB API.
"""

import os
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text

from packages.pms.module_a_context import IST
from packages.pms.module_b_pipeline import ModuleBPipeline, ModuleBResult


@pytest.mark.skipif(
    not os.getenv("PMS_TEST_DATABASE_URL"),
    reason="PMS_TEST_DATABASE_URL not configured",
)
def test_postgres_signal_lifecycle_and_decision_idempotency():
    engine = create_engine(os.environ["PMS_TEST_DATABASE_URL"])
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text("set local search_path=pms,public"))
            instrument = conn.execute(
                text(
                    "select exchange||':'||nse_symbol from instruments where exchange='NSE' and active order by id limit 1"
                )
            ).scalar_one()

            class TransactionEngine:
                @contextmanager
                def begin(self):
                    yield conn

            pipeline = ModuleBPipeline(TransactionEngine())
            pipeline.cfg["technical"][
                "implementation_version"
            ] = "MODULE_B_ROLLBACK_INTEGRATION_TEST"
            version = pipeline.version
            stamp = datetime(2026, 10, 16, 18, tzinfo=IST)
            signal = {
                "signal_date": "2026-10-16",
                "signal_high": 115,
                "signal_low": 100,
                "rally_start": "2026-10-15",
                "rally_high_date": "2026-10-15",
                "rally_pct": 15,
                "velocity": 15,
                "volume_ratio": 1.5,
                "expires_on": "2026-10-22",
                "status": "SIGNAL_CREATED",
            }
            row = ModuleBResult(instrument, date(2026, 10, 16), True, (), signal)
            for _ in range(2):
                pipeline.persist_results([row], stamp)

            def count(table):
                return conn.execute(
                    text(f"select count(*) from {table} where strategy_version=:v"),
                    {"v": version},
                ).scalar_one()

            def status():
                return conn.execute(
                    text(
                        "select status from module_b_signals where strategy_version=:v"
                    ),
                    {"v": version},
                ).scalar_one()

            assert count("strategy_decisions") == 1 and count("module_b_signals") == 1
            conn.execute(
                text(
                    "update module_b_signals set status='CONSUMED' where strategy_version=:v"
                ),
                {"v": version},
            )
            pipeline.persist_results([row], stamp)
            assert status() == "CONSUMED"
            conn.execute(
                text(
                    "update module_b_signals set status='ACTIVE' where strategy_version=:v"
                ),
                {"v": version},
            )
            blocked = ModuleBResult(
                instrument, date(2026, 10, 22), False, ("module_a_ineligible",), None
            )
            pipeline.persist_results([blocked], datetime(2026, 10, 22, 18, tzinfo=IST))
            assert status() == "EXPIRED"
            pipeline.persist_results([row], stamp)
            assert status() == "EXPIRED"
        finally:
            transaction.rollback()
    engine.dispose()
