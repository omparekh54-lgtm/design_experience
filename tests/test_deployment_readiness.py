import copy
import json
from pathlib import Path

import pytest

from packages.pms.config import load_strategy_config
from packages.pms.data_integrity import REQUIRED
from packages.pms.deployment_readiness import (
    assess,
    database_checks,
    environment_checks,
    file_inventory,
    policy_checks,
)
from scripts import check_deployment_readiness as cli

ROOT = Path(__file__).resolve().parents[1]


def cloud_env():
    return dict(
        DATABASE_URL="postgresql://operator:private-db-password@db.example/postgres?sslmode=verify-full",
        OPS_API_KEY="private-operator-token-" * 3,
        PMS_API_BASE_URL="https://api.example",
        EXECUTION_MODE="PAPER",
        LIVE_TRADING_ENABLED="false",
    )


def by_code(checks):
    return {c["code"]: c for c in checks}


def test_cloud_baseline_is_configuration_only_and_never_campaign_ready():
    report = assess(ROOT, load_strategy_config(), cloud_env())
    assert report["status"] == "BLOCKED"
    assert report["campaign_ready"] is False
    assert by_code(report["checks"])["CLOUD_DATA"]["status"] == "NOT_VERIFIED"
    encoded = json.dumps(report)
    assert "private-db-password" not in encoded
    assert "private-operator-token" not in encoded


@pytest.mark.parametrize(
    "override,code",
    [
        (
            {"DATABASE_URL": "postgresql://u:secret@localhost/db?sslmode=verify-full"},
            "DATABASE_CONFIGURATION",
        ),
        (
            {"DATABASE_URL": "postgresql://u:secret@db.example/db?sslmode=require"},
            "DATABASE_CONFIGURATION",
        ),
        (
            {
                "DATABASE_URL": "postgresql://u:YOUR_DATABASE_PASSWORD@db.example/db?sslmode=verify-full"
            },
            "DATABASE_CONFIGURATION",
        ),
        ({"DATABASE_URL": "invalid secret"}, "DATABASE_CONFIGURATION"),
        ({"OPS_API_KEY": "short"}, "OPERATOR_KEY"),
        ({"OPS_API_KEY": " " * 40}, "OPERATOR_KEY"),
        ({"PMS_API_BASE_URL": "http://api.example"}, "FRONTEND_API_ORIGIN"),
        ({"PMS_API_BASE_URL": "https://secret@api.example"}, "FRONTEND_API_ORIGIN"),
        ({"PMS_API_BASE_URL": "https://api.example?key=secret"}, "FRONTEND_API_ORIGIN"),
        ({"EXECUTION_MODE": "LIVE_VALIDATION"}, "PAPER_CONFIGURATION"),
        ({"LIVE_TRADING_ENABLED": "true"}, "PAPER_CONFIGURATION"),
        ({"RUN_REFERENCE_GAP_BACKFILL": "true"}, "STARTUP_BACKFILL"),
        ({"PMS_ALLOW_PRIVATE_HTTP": "1"}, "PRIVATE_HTTP_EXCEPTION"),
    ],
)
def test_unsafe_cloud_configuration_is_blocked_without_echo(override, code):
    env = {**cloud_env(), **override}
    checks = environment_checks(env)
    assert by_code(checks)[code]["status"] == "BLOCKED"
    assert "secret" not in json.dumps(checks)


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True, "2"])
def test_invalid_policy_does_not_become_owner_approval(value):
    cfg = copy.deepcopy(load_strategy_config())
    cfg["fundamental"]["leverage_limits"]["default"] = dict(
        max_debt_to_equity=value, min_interest_coverage=2
    )
    assert by_code(policy_checks(cfg))["LEVERAGE_POLICY"]["status"] == "BLOCKED"


def test_file_inventory_is_deterministic_and_detects_changes(tmp_path):
    (tmp_path / "migrations").mkdir()
    p = tmp_path / "migrations/001.sql"
    p.write_text("select 1")
    a = file_inventory(tmp_path)
    assert a == file_inventory(tmp_path)
    p.write_text("select 2")
    assert a[0]["sha256"] != file_inventory(tmp_path)[0]["sha256"]
    assert "applied" not in a[0]


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        return self.rows


class Connection:
    def __init__(self, *, missing=False):
        self.sql = []
        self.missing = missing

    def execute(self, statement):
        sql = str(statement)
        self.sql.append(sql)
        if "information_schema" in sql:
            return Result(
                []
                if self.missing
                else [
                    dict(table_name=t, column_name=c)
                    for t, columns in REQUIRED.items()
                    for c in columns
                ]
            )
        if "safety_state" in sql:
            return Result(
                [
                    dict(
                        execution_mode="PAPER",
                        new_orders_enabled=False,
                        emergency_stop=False,
                        locked_reason="secret lock detail",
                    )
                ]
            )
        return Result(0)


def test_database_contract_and_restrictive_safety_do_not_clear_campaign_lock():
    conn = Connection()
    checks = by_code(database_checks(conn))
    assert checks["DATABASE_SCHEMA"]["status"] == "PASS"
    assert checks["DATABASE_SAFETY"]["status"] == "PASS"
    assert checks["CAMPAIGN_SAFETY"]["status"] == "BLOCKED"
    assert "secret lock detail" not in json.dumps(checks)
    assert all(sql.lstrip().lower().startswith("select") for sql in conn.sql)


def test_missing_schema_stops_before_querying_missing_tables():
    conn = Connection(missing=True)
    checks = database_checks(conn)
    assert checks[0]["status"] == "BLOCKED"
    assert len(conn.sql) == 1


def test_plan_does_not_load_environment_or_connect(monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(
        dotenv, "load_dotenv", lambda *a, **k: pytest.fail("Plan read dotenv")
    )
    monkeypatch.setenv("DATABASE_URL", "must-never-appear")
    assert cli.main(["--plan"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PLAN"
    assert "must-never-appear" not in json.dumps(report)


def test_database_requires_explicit_check():
    with pytest.raises(SystemExit) as error:
        cli.main(["--plan", "--database"])
    assert error.value.code == 2


def test_check_writes_new_report_and_refuses_overwrite(tmp_path, capsys, monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    target = tmp_path / "report.json"
    assert cli.main(["--check", "--output", str(target)]) == 2
    original = target.read_bytes()
    assert json.loads(original)["campaign_ready"] is False
    with pytest.raises(SystemExit):
        cli.main(["--plan", "--output", str(target)])
    assert target.read_bytes() == original


def test_database_cli_uses_read_only_transaction_and_disposes(monkeypatch, capsys):
    import dotenv
    import packages.pms.database as database

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", cloud_env()["DATABASE_URL"])
    events = []

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("transaction_end")

    class Conn(Context):
        def begin(self):
            events.append("begin")
            return Context()

        def execute(self, sql):
            events.append(str(sql))

    class Engine:
        def connect(self):
            return Conn()

        def dispose(self):
            events.append("disposed")

    monkeypatch.setattr(database, "online_engine", lambda *a, **k: Engine())

    def probe(conn):
        assert events[:2] == ["begin", "SET TRANSACTION READ ONLY"]
        return []

    monkeypatch.setattr(cli, "database_checks", probe)
    assert cli.main(["--check", "--database"]) == 2
    assert events[-1] == "disposed"
    assert json.loads(capsys.readouterr().out)["database_writes"] is False


def test_connection_failure_never_prints_credentials(monkeypatch, capsys):
    import dotenv
    import packages.pms.database as database

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", cloud_env()["DATABASE_URL"])

    def fail(*a, **k):
        raise RuntimeError("private-db-password from SQL driver")

    monkeypatch.setattr(database, "online_engine", fail)
    assert cli.main(["--check", "--database"]) == 2
    output = capsys.readouterr().out
    assert "private-db-password" not in output
    assert "RuntimeError" in output
